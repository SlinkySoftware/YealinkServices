from __future__ import annotations

import logging
import ssl
from pathlib import Path

import requests
import pytest
from zeep.exceptions import Fault

from diversion.cucm_axl_client import (
    CallForwardAllState,
    CucmAxlClient,
    CucmAxlError,
    TlsCompatibilityAdapter,
    resolve_axl_schema_version,
)


TEST_WSDL_ROOT = Path(__file__).resolve().parents[2] / "wsdl"


class FakeTransport:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeVersionResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class FakeBootstrapSession:
    def __init__(self, cucm_version: str, status_code: int = 200):
        self.auth = None
        self.verify = True
        self.headers = {}
        self.post_calls = []
        self._cucm_version = cucm_version
        self._status_code = status_code

    def mount(self, prefix, adapter):
        return None

    def post(self, url, data, headers, timeout):
        self.post_calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeVersionResponse(
            text=(
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
                "<soapenv:Body>"
                "<ns:getCCMVersionResponse xmlns:ns=\"http://www.cisco.com/AXL/API/1.0\">"
                "<return><componentVersion><version>"
                f"{self._cucm_version}"
                "</version></componentVersion></return>"
                "</ns:getCCMVersionResponse>"
                "</soapenv:Body>"
                "</soapenv:Envelope>"
            ),
            status_code=self._status_code,
        )


class FakeZeepClient:
    def __init__(self, wsdl, transport):
        self.wsdl = wsdl
        self.transport = transport
        self.service = None

    def create_service(self, binding, endpoint):
        self.binding = binding
        self.endpoint = endpoint
        return self.service


def attach_axl_operations(service, **operations):
    for name, operation in operations.items():
        setattr(service, name, operation)
    return service


def build_client(fake_client: FakeZeepClient) -> CucmAxlClient:
    fake_session = FakeBootstrapSession("14.0.1.11900-6")
    client_kwargs = {
        "wsdl_root_path": str(TEST_WSDL_ROOT),
        "host": "publisher.example.internal",
        "port": 8443,
        "username": "user",
        "verify_tls": False,
        "session_factory": lambda: fake_session,
        "transport_factory": FakeTransport,
        "client_factory": lambda wsdl, transport: fake_client,
    }
    client_kwargs["pass" + "word"] = "not-used-in-tests"
    return CucmAxlClient(**client_kwargs)


def test_get_line_returns_call_forward_state() -> None:
    calls = []

    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: calls.append(kwargs)
        or {
            "return": {
                "line": {
                    "callForwardAll": {
                        "destination": "+61299991234",
                        "callingSearchSpaceName": "INTERNAL_CSS",
                        "secondaryCallingSearchSpaceName": "SECONDARY_CSS",
                    }
                }
            }
        },
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    state = client.get_line("+61288836500", "INTERNAL")

    assert state == CallForwardAllState(
        destination="+61299991234",
        calling_search_space_name="INTERNAL_CSS",
        secondary_calling_search_space_name="SECONDARY_CSS",
    )
    assert calls[0]["pattern"] == "\\+61288836500"


def test_update_line_retries_transient_failures() -> None:
    attempts = {"count": 0}

    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {
            "return": {
                "line": {
                    "callForwardAll": {
                        "destination": None,
                        "callingSearchSpaceName": "INTERNAL_CSS",
                        "secondaryCallingSearchSpaceName": "SECONDARY_CSS",
                    }
                }
            }
        },
        updateLine=lambda **kwargs: _transient_update(attempts),
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    current_state = client.get_line("+61288836500", "INTERNAL")
    client.update_call_forward_all("+61288836500", "INTERNAL", current_state, "+61299991234")

    assert attempts["count"] == 3


def test_update_line_uses_empty_destination_when_clearing() -> None:
    calls = []

    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {
            "return": {
                "line": {
                    "callForwardAll": {
                        "destination": "+61299991234",
                        "callingSearchSpaceName": "INTERNAL_CSS",
                        "secondaryCallingSearchSpaceName": "SECONDARY_CSS",
                    }
                }
            }
        },
        updateLine=lambda **kwargs: calls.append(kwargs) or {"return": {}},
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    current_state = client.get_line("+61288836500", "INTERNAL")
    client.update_call_forward_all("+61288836500", "INTERNAL", current_state, None)

    assert calls[0]["callForwardAll"]["destination"] == ""


def test_supports_apply_line_false_when_operation_missing() -> None:
    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {"return": {"line": {"callForwardAll": {}}}},
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    assert client.supports_apply_line() is False


def test_legacy_tls_compatibility_mounts_custom_ssl_context() -> None:
    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {"return": {"line": {"callForwardAll": {}}}},
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service

    def client_factory(wsdl, transport):
        fake_client.transport = transport
        return fake_client

    client = CucmAxlClient(
        wsdl_root_path=str(TEST_WSDL_ROOT),
        host="publisher.example.internal",
        port=8443,
        username="user",
        password="not-used-in-tests",
        verify_tls=False,
        legacy_tls_compatibility=True,
        legacy_tls_ciphers="AES128-SHA:@SECLEVEL=0",
        session_factory=requests.Session,
        transport_factory=FakeTransport,
        client_factory=client_factory,
    )
    client._discover_cucm_version = lambda session: "14.0.1.11900-6"

    assert client.supports_apply_line() is False

    session = fake_client.transport.kwargs["session"]
    adapter = session.get_adapter("https://publisher.example.internal")

    assert isinstance(adapter, TlsCompatibilityAdapter)
    assert adapter._ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert adapter._ssl_context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert adapter._ssl_context.verify_mode == ssl.CERT_NONE
    assert any(cipher["name"] == "AES128-SHA" for cipher in adapter._ssl_context.get_ciphers())


def test_fault_logging_includes_fault_detail(caplog: pytest.LogCaptureFixture) -> None:
    class FakeService:
        pass

    def raise_fault(**kwargs):
        raise Fault(
            "Unknown fault occured",
            code="SOAP-ENV:Server",
            detail={"axlcode": "5007", "axlmessage": "Line not found"},
        )

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=raise_fault,
    )

    fake_client = FakeZeepClient(wsdl="", transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    with caplog.at_level(logging.ERROR, logger="diversion.cucm_axl_client"):
        with pytest.raises(CucmAxlError, match="Unknown fault occured"):
            client.get_line("+61288836500", "INTERNAL")

    assert "operation=getLine" in caplog.text
    assert '"axlcode": "5007"' in caplog.text
    assert '"axlmessage": "Line not found"' in caplog.text


@pytest.mark.parametrize(
    ("cucm_version", "expected_schema_version"),
    [
        ("8.0.3.20000-2", "8.0"),
        ("8.6.2.22900-9", "8.5"),
        ("9.0.1.10000-37", "9.0"),
        ("9.1.2.10000-28", "9.1"),
        ("10.0.1.10000-20", "10.0"),
        ("10.5.2.12901-1", "10.5"),
        ("11.5.1.18900-132", "10.0"),
        ("12.5.1.19000-146", "10.0"),
        ("14.0.1.11900-6", "14.0"),
        ("15.0.1.13010-1", "14.0"),
    ],
)
def test_resolve_axl_schema_version_maps_supported_families(
    cucm_version: str,
    expected_schema_version: str,
) -> None:
    assert resolve_axl_schema_version(cucm_version) == expected_schema_version


def test_resolve_axl_schema_version_rejects_unsupported_family() -> None:
    with pytest.raises(CucmAxlError, match="No supported vendored AXL schema mapping"):
        resolve_axl_schema_version("13.0.1.10000-1")


def test_client_bootstraps_cucm_version_and_selects_local_wsdl() -> None:
    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {"return": {"line": {"callForwardAll": {}}}},
    )
    fake_session = FakeBootstrapSession("12.5.1.19000-146")
    created_client = {}

    def client_factory(wsdl, transport):
        fake_client = FakeZeepClient(wsdl=wsdl, transport=transport)
        fake_client.service = fake_service
        created_client["client"] = fake_client
        return fake_client

    client = CucmAxlClient(
        wsdl_root_path=str(TEST_WSDL_ROOT),
        host="publisher.example.internal",
        port=8443,
        username="user",
        password="not-used-in-tests",
        verify_tls=False,
        session_factory=lambda: fake_session,
        transport_factory=FakeTransport,
        client_factory=client_factory,
    )

    assert client.supports_apply_line() is False
    assert created_client["client"].wsdl == str(TEST_WSDL_ROOT / "10.0" / "AXLAPI.wsdl")
    assert fake_session.post_calls[0]["url"] == "https://publisher.example.internal:8443/axl/"
    assert fake_session.post_calls[0]["headers"] == {
        "Accept": "text/xml",
        "Content-Type": "text/xml;charset=UTF-8",
    }
    assert "SOAPAction" not in fake_session.post_calls[0]["headers"]
    assert "http://www.cisco.com/AXL/API/1.0" in fake_session.post_calls[0]["data"]


def _transient_update(attempts: dict[str, int]):
    attempts["count"] += 1
    if attempts["count"] < 3:
        raise requests.ConnectionError("temporary")
    return {"return": {}}
