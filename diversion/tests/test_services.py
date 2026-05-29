from __future__ import annotations

from dataclasses import dataclass

import pytest

from diversion.cache import InMemoryCfaCache
from diversion.cucm_axl_client import CallForwardAllState
from diversion.phone_manager_client import DeviceContext
from diversion.services import CallDiversionService, RequestAuditContext


class FakePhoneManagerClient:
    def __init__(self, normalized_destination: str = "+61299991234") -> None:
        self.normalized_destination = normalized_destination

    def validate_device_context(self, mac: str, dn: str, token: str) -> DeviceContext:
        return DeviceContext(
            mac="805EC0ABCDEF",
            model="SIP-T33G",
            dn=dn,
            sip_username=None,
            line_count=1,
            device_name=None,
            site="2SYA",
            dial_plan_id=None,
            message="OK",
        )

    def normalize_number(self, mac: str, dn: str, token: str, entered_destination: str) -> str:
        return self.normalized_destination


class FakeCucmClient:
    def __init__(self, state: CallForwardAllState) -> None:
        self.state = state
        self.update_calls = 0
        self.apply_calls = 0

    def get_line(self, pattern: str, route_partition_name: str) -> CallForwardAllState:
        return self.state

    def update_call_forward_all(
        self,
        pattern: str,
        route_partition_name: str,
        current_state: CallForwardAllState,
        destination: str | None,
    ) -> None:
        self.update_calls += 1
        self.state = CallForwardAllState(
            destination=destination,
            calling_search_space_name=current_state.calling_search_space_name,
            secondary_calling_search_space_name=current_state.secondary_calling_search_space_name,
            forward_to_voice_mail=False,
        )

    def apply_line(self, pattern: str, route_partition_name: str) -> None:
        self.apply_calls += 1

    def supports_apply_line(self) -> bool:
        return True


AUDIT_CONTEXT = RequestAuditContext(request_id="req-123", source_ip="127.0.0.1")


def test_enable_diversion_idempotent_when_destination_matches() -> None:
    current_state = CallForwardAllState(
        destination="+61299991234",
        calling_search_space_name="INTERNAL_CSS",
        secondary_calling_search_space_name="SECONDARY_CSS",
        forward_to_voice_mail=False,
    )
    cucm_client = FakeCucmClient(current_state)
    service = CallDiversionService(
        phone_manager_client=FakePhoneManagerClient(normalized_destination="+61299991234"),
        cucm_axl_client=cucm_client,
        route_partition_name="INTERNAL",
        cfa_cache=InMemoryCfaCache(3600),
        dry_run=False,
        apply_line_after_update=True,
    )

    result = service.enable_diversion(
        "805EC0ABCDEF",
        "+61288836500",
        "abcd1234",
        "0299991234",
        AUDIT_CONTEXT,
    )

    assert result.idempotent is True
    assert cucm_client.update_calls == 0


def test_disable_diversion_returns_status_when_already_disabled() -> None:
    current_state = CallForwardAllState(
        destination=None,
        calling_search_space_name="INTERNAL_CSS",
        secondary_calling_search_space_name="SECONDARY_CSS",
        forward_to_voice_mail=False,
    )
    service = CallDiversionService(
        phone_manager_client=FakePhoneManagerClient(),
        cucm_axl_client=FakeCucmClient(current_state),
        route_partition_name="INTERNAL",
        cfa_cache=InMemoryCfaCache(3600),
        dry_run=False,
        apply_line_after_update=True,
    )

    result = service.disable_diversion(
        "805EC0ABCDEF",
        "+61288836500",
        "abcd1234",
        AUDIT_CONTEXT,
    )

    assert result.already_disabled is True
    assert result.resulting_state.destination is None


def test_dry_run_enable_skips_update_line() -> None:
    current_state = CallForwardAllState(
        destination=None,
        calling_search_space_name="INTERNAL_CSS",
        secondary_calling_search_space_name="SECONDARY_CSS",
        forward_to_voice_mail=False,
    )
    cucm_client = FakeCucmClient(current_state)
    service = CallDiversionService(
        phone_manager_client=FakePhoneManagerClient(normalized_destination="+61299991234"),
        cucm_axl_client=cucm_client,
        route_partition_name="INTERNAL",
        cfa_cache=InMemoryCfaCache(3600),
        dry_run=True,
        apply_line_after_update=True,
    )

    result = service.enable_diversion(
        "805EC0ABCDEF",
        "+61288836500",
        "abcd1234",
        "0299991234",
        AUDIT_CONTEXT,
    )

    assert result.dry_run is True
    assert cucm_client.update_calls == 0
    assert cucm_client.apply_calls == 0


def test_enable_diversion_rejects_short_destination() -> None:
    service = CallDiversionService(
        phone_manager_client=FakePhoneManagerClient(),
        cucm_axl_client=FakeCucmClient(
            CallForwardAllState(
                destination=None,
                calling_search_space_name="INTERNAL_CSS",
                secondary_calling_search_space_name="SECONDARY_CSS",
                forward_to_voice_mail=False,
            )
        ),
        route_partition_name="INTERNAL",
        cfa_cache=InMemoryCfaCache(3600),
        dry_run=False,
        apply_line_after_update=True,
    )

    with pytest.raises(Exception):
        service.enable_diversion(
            "805EC0ABCDEF",
            "+61288836500",
            "abcd1234",
            "1234",
            AUDIT_CONTEXT,
        )
