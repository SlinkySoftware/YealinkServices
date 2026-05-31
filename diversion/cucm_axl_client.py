from __future__ import annotations

import json
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
import urllib3
from requests import Session
from requests.adapters import HTTPAdapter
from zeep import Client
from zeep.exceptions import Fault, TransportError
from zeep.helpers import serialize_object
from zeep.transports import Transport


AXL_BINDING = "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding"
TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_LEGACY_TLS_CIPHERS = "AES128-SHA:@SECLEVEL=0"
LOGGER = logging.getLogger(__name__)


class TlsCompatibilityAdapter(HTTPAdapter):
    def __init__(self, ssl_context: ssl.SSLContext, **kwargs: Any) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        pool_kwargs["ssl_context"] = self._ssl_context
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class CucmAxlError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallForwardAllState:
    destination: str | None
    calling_search_space_name: str | None
    secondary_calling_search_space_name: str | None
    forward_to_voice_mail: bool

    @property
    def enabled(self) -> bool:
        return bool(self.destination)


class CucmAxlClient:
    def __init__(
        self,
        wsdl_path: str,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_tls: bool,
        timeout_seconds: int = 10,
        legacy_tls_compatibility: bool = False,
        legacy_tls_ciphers: str = DEFAULT_LEGACY_TLS_CIPHERS,
        session_factory: Callable[[], Session] = requests.Session,
        transport_factory: type[Transport] = Transport,
        client_factory: type[Client] = Client,
    ) -> None:
        self._wsdl_path = wsdl_path
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._verify_tls = verify_tls
        self._timeout_seconds = timeout_seconds
        self._legacy_tls_compatibility = legacy_tls_compatibility
        self._legacy_tls_ciphers = legacy_tls_ciphers
        self._session_factory = session_factory
        self._transport_factory = transport_factory
        self._client_factory = client_factory
        self._client: Any | None = None
        self._service: Any | None = None

    @property
    def endpoint(self) -> str:
        return f"https://{self._host}:{self._port}/axl/"

    def supports_apply_line(self) -> bool:
        service = self._ensure_service()
        return hasattr(service, "applyLine")

    def get_line(self, pattern: str, route_partition_name: str) -> CallForwardAllState:
        response = self._call_operation(
            "getLine",
            pattern=self._escape_pattern(pattern),
            routePartitionName=route_partition_name,
            returnedTags={
                "pattern": "",
                "routePartitionName": "",
                "callForwardAll": {
                    "destination": "",
                    "callingSearchSpaceName": "",
                    "secondaryCallingSearchSpaceName": "",
                    "forwardToVoiceMail": "",
                },
            },
        )
        payload = serialize_object(response)
        try:
            line = payload["return"]["line"]
        except (KeyError, TypeError) as exc:
            LOGGER.exception(
                "Unexpected CUCM getLine response structure endpoint=%s payload=%s",
                self.endpoint,
                self._format_for_log(payload),
            )
            raise CucmAxlError("Unexpected getLine response structure") from exc

        call_forward_all = line.get("callForwardAll") or {}
        destination = self._as_optional_string(call_forward_all.get("destination"))

        return CallForwardAllState(
            destination=destination,
            calling_search_space_name=self._fk_type_to_string(
                call_forward_all.get("callingSearchSpaceName")
            ),
            secondary_calling_search_space_name=self._fk_type_to_string(
                call_forward_all.get("secondaryCallingSearchSpaceName")
            ),
            forward_to_voice_mail=bool(call_forward_all.get("forwardToVoiceMail", False)),
        )

    def update_call_forward_all(
        self,
        pattern: str,
        route_partition_name: str,
        current_state: CallForwardAllState,
        destination: str | None,
    ) -> None:
        self._call_operation(
            "updateLine",
            pattern=self._escape_pattern(pattern),
            routePartitionName=route_partition_name,
            callForwardAll={
                "forwardToVoiceMail": False,
                "callingSearchSpaceName": current_state.calling_search_space_name,
                "secondaryCallingSearchSpaceName": current_state.secondary_calling_search_space_name,
                "destination": destination,
            },
        )

    def apply_line(self, pattern: str, route_partition_name: str) -> None:
        if not self.supports_apply_line():
            return
        self._call_operation(
            "applyLine",
            pattern=self._escape_pattern(pattern),
            routePartitionName=route_partition_name,
        )

    def _call_operation(self, operation_name: str, **kwargs: Any) -> Any:
        service = self._ensure_service()
        operation = getattr(service, operation_name)

        last_exception: Exception | None = None
        for attempt in range(1, 4):
            try:
                return operation(**kwargs)
            except Fault as exc:
                LOGGER.exception(
                    (
                        "CUCM AXL fault operation=%s endpoint=%s attempt=%s "
                        "kwargs=%s code=%r actor=%r subcodes=%r detail=%s"
                    ),
                    operation_name,
                    self.endpoint,
                    attempt,
                    self._format_for_log(kwargs),
                    getattr(exc, "code", None),
                    getattr(exc, "actor", None),
                    getattr(exc, "subcodes", None),
                    self._format_for_log(getattr(exc, "detail", None)),
                )
                raise CucmAxlError(f"CUCM AXL fault on {operation_name}: {exc}") from exc
            except TransportError as exc:
                if not self._is_transient_transport_error(exc) or attempt == 3:
                    LOGGER.exception(
                        (
                            "CUCM transport error operation=%s endpoint=%s attempt=%s "
                            "kwargs=%s status_code=%r content=%s"
                        ),
                        operation_name,
                        self.endpoint,
                        attempt,
                        self._format_for_log(kwargs),
                        getattr(exc, "status_code", None),
                        self._format_for_log(getattr(exc, "content", None)),
                    )
                    raise CucmAxlError(f"CUCM transport error on {operation_name}: {exc}") from exc
                last_exception = exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == 3:
                    LOGGER.exception(
                        "CUCM connection error operation=%s endpoint=%s attempt=%s kwargs=%s",
                        operation_name,
                        self.endpoint,
                        attempt,
                        self._format_for_log(kwargs),
                    )
                    raise CucmAxlError(f"CUCM connection error on {operation_name}: {exc}") from exc
                last_exception = exc

        raise CucmAxlError(
            f"CUCM AXL operation {operation_name} failed after retries: {last_exception}"
        )

    def _ensure_service(self) -> Any:
        if self._service is not None:
            return self._service

        session = self._session_factory()
        session.auth = (self._username, self._password)
        session.verify = self._verify_tls
        session.headers.update({"User-Agent": "yealinkService/1.0"})
        if not self._verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        if self._legacy_tls_compatibility:
            session.mount("https://", TlsCompatibilityAdapter(self._build_ssl_context()))

        transport = self._transport_factory(
            session=session,
            timeout=self._timeout_seconds,
            operation_timeout=self._timeout_seconds,
        )
        self._client = self._client_factory(wsdl=self._wsdl_path, transport=transport)
        self._service = self._client.create_service(AXL_BINDING, self.endpoint)
        return self._service

    @staticmethod
    def _is_transient_transport_error(exc: TransportError) -> bool:
        status_code = getattr(exc, "status_code", None)
        return status_code is None or status_code in TRANSIENT_HTTP_CODES

    def _build_ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers(self._legacy_tls_ciphers)
        if not self._verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    @staticmethod
    def _escape_pattern(pattern: str) -> str:
        cleaned = pattern.strip()
        if cleaned.startswith("\\+"):
            return cleaned
        if cleaned.startswith("+"):
            return f"\\{cleaned}"
        return cleaned

    @staticmethod
    def _as_optional_string(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _fk_type_to_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return cls._as_optional_string(value)
        if isinstance(value, dict):
            return cls._as_optional_string(value.get("_value_1") or value.get("value"))
        extracted = getattr(value, "_value_1", None)
        if extracted is not None:
            return cls._as_optional_string(extracted)
        return cls._as_optional_string(value)

    @staticmethod
    def _format_for_log(value: object) -> str:
        try:
            serialized = serialize_object(value)
        except Exception:
            serialized = value

        try:
            return json.dumps(serialized, default=str, sort_keys=True)
        except TypeError:
            return repr(serialized)
