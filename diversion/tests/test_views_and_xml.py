from __future__ import annotations

from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings

from diversion.cucm_axl_client import CallForwardAllState
from diversion.phone_manager_client import DeviceContext
from diversion.services import DiversionStatus
from diversion.yealink_xml import HandsetRequestParams, build_screen_context, parse_handset_request


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "yealink"


def example_params() -> HandsetRequestParams:
    return HandsetRequestParams(
        mac="805EC0ABCDEF",
        dn="+61288836500",
        token="abcd1234",
    )


def base_context(**extra):
    return build_screen_context(
        example_params(),
        dn="+61288836500",
        normalized_destination="+61299991234",
        default_destination="+61299991234",
        **extra,
    )


@override_settings(
    PHONE_SERVICES_BASE_URL="http://phoneservices.example.internal/services/",
    PHONE_SERVICES_COMPANY_NAME="ExampleCorp",
)
@pytest.mark.parametrize(
    ("template_name", "fixture_name"),
    [
        ("yealink/status_off.xml", "status_off.xml"),
        ("yealink/status_on.xml", "status_on.xml"),
        ("yealink/input_destination.xml", "input_destination.xml"),
        ("yealink/success_enabled.xml", "success_enabled.xml"),
        ("yealink/success_disabled.xml", "success_disabled.xml"),
        ("yealink/error_unavailable.xml", "error_unavailable.xml"),
        ("yealink/error_invalid_destination.xml", "error_invalid_destination.xml"),
    ],
)
def test_templates_render_expected_xml(template_name, fixture_name) -> None:
    context = base_context()
    rendered = render_to_string(template_name, context).strip()
    expected = (FIXTURE_DIR / fixture_name).read_text().strip()
    assert rendered == expected


@override_settings(
    PHONE_SERVICES_BASE_URL="http://phoneservices.example.internal/services/",
    PHONE_SERVICES_COMPANY_NAME="ExampleCorp",
)
def test_status_view_missing_token_returns_unavailable(monkeypatch) -> None:
    client = Client()
    response = client.get("/services/", {"mac": "805EC0ABCDEF", "dn": "+61288836500"})
    assert response.status_code == 200
    assert b"Call Diversion is Unavailable" in response.content


@override_settings(
    PHONE_SERVICES_BASE_URL="http://phoneservices.example.internal/services/",
    PHONE_SERVICES_COMPANY_NAME="ExampleCorp",
)
def test_set_view_missing_destination_returns_invalid_destination() -> None:
    client = Client()
    response = client.get(
        "/services/set/",
        {"mac": "805EC0ABCDEF", "dn": "+61288836500", "token": "abcd1234"},
    )
    assert response.status_code == 200
    assert b"Invalid Destination Specified" in response.content


@override_settings(
    PHONE_SERVICES_BASE_URL="http://phoneservices.example.internal/services/",
    PHONE_SERVICES_COMPANY_NAME="ExampleCorp",
)
def test_status_view_renders_diverted_screen(monkeypatch) -> None:
    class FakeService:
        def get_status(self, mac, dn, token, audit_context, bypass_cache=False, action="status"):
            return DiversionStatus(
                device_context=DeviceContext(
                    mac="805EC0ABCDEF",
                    model="SIP-T33G",
                    dn="+61288836500",
                    sip_username=None,
                    line_count=1,
                    device_name=None,
                    site="2SYA",
                    dial_plan_id=None,
                    message="OK",
                ),
                line_state=CallForwardAllState(
                    destination="+61299991234",
                    calling_search_space_name="INTERNAL_CSS",
                    secondary_calling_search_space_name="SECONDARY_CSS",
                    forward_to_voice_mail=False,
                ),
            )

    monkeypatch.setattr("diversion.views.get_diversion_service", lambda: FakeService())

    client = Client()
    response = client.get(
        "/services/",
        {"mac": "805EC0ABCDEF", "dn": "+61288836500", "token": "abcd1234"},
    )
    assert response.status_code == 200
    assert b"Status: Diverted" in response.content
    assert b"+61299991234" in response.content


@override_settings(
    PHONE_SERVICES_BASE_URL="http://phoneservices.example.internal/services/",
    PHONE_SERVICES_COMPANY_NAME="ExampleCorp",
)
@pytest.mark.parametrize(
    "request_mac",
    [
        "C4:FC:22:4C:78:17",
        "C4%3AFC%3A22%3A4C%3A78%3A17",
    ],
)
def test_parse_handset_request_normalizes_mac_and_generated_urls(request_mac: str) -> None:
    request = RequestFactory().get(
        "/services/",
        {"mac": request_mac, "dn": "+61288836500", "token": "abcd1234"},
    )

    params = parse_handset_request(request)
    context = build_screen_context(params)

    assert params.mac == "C4FC224C7817"
    assert "mac=C4FC224C7817" in context["enable_url"]
    assert "%3A" not in context["enable_url"]


@override_settings(AXL_WSDL_ROOT=Path(__file__).resolve().parents[2] / "wsdl")
def test_health_view_reports_vendored_wsdl_versions() -> None:
    client = Client()
    response = client.get("/services/health/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["wsdl_present"] is True
    assert payload["wsdl_versions"] == ["8.0", "8.5", "9.0", "9.1", "10.0", "10.5", "14.0"]
