from __future__ import annotations

import ssl
from pathlib import Path

import requests

from diversion.cucm_axl_client import CallForwardAllState, CucmAxlClient, TlsCompatibilityAdapter


TEST_WSDL_PATH = str(Path(__file__).resolve().parent / "fixtures" / "AXLAPI.wsdl")


class FakeTransport:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


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
    client_kwargs = {
        "wsdl_path": TEST_WSDL_PATH,
        "host": "publisher.example.internal",
        "port": 8443,
        "username": "user",
        "verify_tls": False,
        "session_factory": requests.Session,
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
                        "forwardToVoiceMail": False,
                    }
                }
            }
        },
    )

    fake_client = FakeZeepClient(wsdl=TEST_WSDL_PATH, transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    state = client.get_line("+61288836500", "INTERNAL")

    assert state == CallForwardAllState(
        destination="+61299991234",
        calling_search_space_name="INTERNAL_CSS",
        secondary_calling_search_space_name="SECONDARY_CSS",
        forward_to_voice_mail=False,
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
                        "forwardToVoiceMail": False,
                    }
                }
            }
        },
        updateLine=lambda **kwargs: _transient_update(attempts),
    )

    fake_client = FakeZeepClient(wsdl=TEST_WSDL_PATH, transport=FakeTransport())
    fake_client.service = fake_service
    client = build_client(fake_client)

    current_state = client.get_line("+61288836500", "INTERNAL")
    client.update_call_forward_all("+61288836500", "INTERNAL", current_state, "+61299991234")

    assert attempts["count"] == 3


def test_supports_apply_line_false_when_operation_missing() -> None:
    class FakeService:
        pass

    fake_service = attach_axl_operations(
        FakeService(),
        getLine=lambda **kwargs: {"return": {"line": {"callForwardAll": {}}}},
    )

    fake_client = FakeZeepClient(wsdl=TEST_WSDL_PATH, transport=FakeTransport())
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

    fake_client = FakeZeepClient(wsdl=TEST_WSDL_PATH, transport=FakeTransport())
    fake_client.service = fake_service

    def client_factory(wsdl, transport):
        fake_client.transport = transport
        return fake_client

    client = CucmAxlClient(
        wsdl_path=TEST_WSDL_PATH,
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

    assert client.supports_apply_line() is False

    session = fake_client.transport.kwargs["session"]
    adapter = session.get_adapter("https://publisher.example.internal")

    assert isinstance(adapter, TlsCompatibilityAdapter)
    assert adapter._ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert adapter._ssl_context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert adapter._ssl_context.verify_mode == ssl.CERT_NONE
    assert any(cipher["name"] == "AES128-SHA" for cipher in adapter._ssl_context.get_ciphers())


def _transient_update(attempts: dict[str, int]):
    attempts["count"] += 1
    if attempts["count"] < 3:
        raise requests.ConnectionError("temporary")
    return {"return": {}}
