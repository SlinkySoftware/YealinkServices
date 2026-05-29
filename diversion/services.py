from __future__ import annotations

import re
from dataclasses import dataclass, replace

from django.conf import settings

from .cache import InMemoryCfaCache, build_cache_key
from .cucm_axl_client import CallForwardAllState, CucmAxlClient, CucmAxlError
from .logging_utils import log_audit
from .phone_manager_client import (
    DeviceContext,
    InvalidDestinationError as PhoneManagerInvalidDestination,
    PhoneManagerClient,
    PhoneManagerUnavailable,
    PhoneManagerValidationError,
)


class DiversionServiceUnavailable(RuntimeError):
    pass


class DiversionInvalidDestination(RuntimeError):
    pass


@dataclass(frozen=True)
class RequestAuditContext:
    request_id: str
    source_ip: str


@dataclass(frozen=True)
class DiversionStatus:
    device_context: DeviceContext
    line_state: CallForwardAllState


@dataclass(frozen=True)
class EnableResult:
    device_context: DeviceContext
    normalized_destination: str
    previous_destination: str | None
    resulting_state: CallForwardAllState
    dry_run: bool
    idempotent: bool


@dataclass(frozen=True)
class DisableResult:
    device_context: DeviceContext
    previous_destination: str | None
    resulting_state: CallForwardAllState
    dry_run: bool
    already_disabled: bool


class CallDiversionService:
    def __init__(
        self,
        phone_manager_client: PhoneManagerClient,
        cucm_axl_client: CucmAxlClient,
        route_partition_name: str,
        cfa_cache: InMemoryCfaCache[CallForwardAllState],
        dry_run: bool,
        apply_line_after_update: bool,
    ) -> None:
        self._phone_manager_client = phone_manager_client
        self._cucm_axl_client = cucm_axl_client
        self._route_partition_name = route_partition_name
        self._cfa_cache = cfa_cache
        self._dry_run = dry_run
        self._apply_line_after_update = apply_line_after_update

    def get_status(
        self,
        mac: str,
        dn: str,
        token: str,
        audit_context: RequestAuditContext,
        *,
        bypass_cache: bool = False,
        action: str = "status",
    ) -> DiversionStatus:
        try:
            device_context = self._phone_manager_client.validate_device_context(mac, dn, token)
            line_state = self._get_line_state(device_context, bypass_cache=bypass_cache)
        except (PhoneManagerUnavailable, PhoneManagerValidationError, CucmAxlError) as exc:
            log_audit(
                request_id=audit_context.request_id,
                source_ip=audit_context.source_ip,
                mac=mac,
                dn=dn,
                model=None,
                action=action,
                entered_destination=None,
                normalized_destination=None,
                old_cfa_destination=None,
                new_cfa_destination=None,
                result="error",
                error_summary=str(exc),
                axl_operation="getLine",
            )
            raise DiversionServiceUnavailable(str(exc)) from exc

        log_audit(
            request_id=audit_context.request_id,
            source_ip=audit_context.source_ip,
            mac=device_context.mac,
            dn=device_context.dn,
            model=device_context.model,
            action=action,
            entered_destination=None,
            normalized_destination=None,
            old_cfa_destination=line_state.destination,
            new_cfa_destination=line_state.destination,
            result="success",
            error_summary=None,
            axl_operation="getLine",
        )
        return DiversionStatus(device_context=device_context, line_state=line_state)

    def enable_diversion(
        self,
        mac: str,
        dn: str,
        token: str,
        entered_destination: str,
        audit_context: RequestAuditContext,
    ) -> EnableResult:
        self._validate_destination(entered_destination)

        try:
            device_context = self._phone_manager_client.validate_device_context(mac, dn, token)
            normalized_destination = self._phone_manager_client.normalize_number(
                device_context.mac,
                device_context.dn,
                token,
                entered_destination,
            )
            current_state = self._cucm_axl_client.get_line(
                device_context.dn,
                self._route_partition_name,
            )
        except PhoneManagerInvalidDestination as exc:
            self._log_invalid_destination(
                audit_context,
                mac,
                dn,
                entered_destination,
                str(exc),
            )
            raise DiversionInvalidDestination(str(exc)) from exc
        except (PhoneManagerUnavailable, PhoneManagerValidationError, CucmAxlError) as exc:
            self._log_unavailable(
                audit_context,
                mac,
                dn,
                action="enable",
                entered_destination=entered_destination,
                error_summary=str(exc),
            )
            raise DiversionServiceUnavailable(str(exc)) from exc

        if current_state.destination == normalized_destination:
            self._store_cache(device_context, current_state)
            log_audit(
                request_id=audit_context.request_id,
                source_ip=audit_context.source_ip,
                mac=device_context.mac,
                dn=device_context.dn,
                model=device_context.model,
                action="enable",
                entered_destination=entered_destination,
                normalized_destination=normalized_destination,
                old_cfa_destination=current_state.destination,
                new_cfa_destination=current_state.destination,
                result="idempotent",
                error_summary=None,
                axl_operation="getLine",
            )
            return EnableResult(
                device_context=device_context,
                normalized_destination=normalized_destination,
                previous_destination=current_state.destination,
                resulting_state=current_state,
                dry_run=False,
                idempotent=True,
            )

        if self._dry_run:
            simulated_state = replace(
                current_state,
                destination=normalized_destination,
                forward_to_voice_mail=False,
            )
            log_audit(
                request_id=audit_context.request_id,
                source_ip=audit_context.source_ip,
                mac=device_context.mac,
                dn=device_context.dn,
                model=device_context.model,
                action="enable",
                entered_destination=entered_destination,
                normalized_destination=normalized_destination,
                old_cfa_destination=current_state.destination,
                new_cfa_destination=normalized_destination,
                result="dry-run",
                error_summary=None,
                axl_operation="getLine",
            )
            return EnableResult(
                device_context=device_context,
                normalized_destination=normalized_destination,
                previous_destination=current_state.destination,
                resulting_state=simulated_state,
                dry_run=True,
                idempotent=False,
            )

        try:
            self._cucm_axl_client.update_call_forward_all(
                device_context.dn,
                self._route_partition_name,
                current_state=current_state,
                destination=normalized_destination,
            )
            axl_operation = "updateLine"
            if self._apply_line_after_update and self._cucm_axl_client.supports_apply_line():
                self._cucm_axl_client.apply_line(device_context.dn, self._route_partition_name)
                axl_operation = "updateLine/applyLine"
            confirmed_state = self._cucm_axl_client.get_line(
                device_context.dn,
                self._route_partition_name,
            )
        except CucmAxlError as exc:
            self._log_unavailable(
                audit_context,
                device_context.mac,
                device_context.dn,
                action="enable",
                entered_destination=entered_destination,
                error_summary=str(exc),
            )
            raise DiversionServiceUnavailable(str(exc)) from exc

        if confirmed_state.destination != normalized_destination:
            self._log_unavailable(
                audit_context,
                device_context.mac,
                device_context.dn,
                action="enable",
                entered_destination=entered_destination,
                error_summary="CUCM confirmation mismatch after updateLine",
            )
            raise DiversionServiceUnavailable("CUCM confirmation mismatch after updateLine")

        self._store_cache(device_context, confirmed_state)
        log_audit(
            request_id=audit_context.request_id,
            source_ip=audit_context.source_ip,
            mac=device_context.mac,
            dn=device_context.dn,
            model=device_context.model,
            action="enable",
            entered_destination=entered_destination,
            normalized_destination=normalized_destination,
            old_cfa_destination=current_state.destination,
            new_cfa_destination=confirmed_state.destination,
            result="success",
            error_summary=None,
            axl_operation=axl_operation + "/getLine",
        )
        return EnableResult(
            device_context=device_context,
            normalized_destination=normalized_destination,
            previous_destination=current_state.destination,
            resulting_state=confirmed_state,
            dry_run=False,
            idempotent=False,
        )

    def disable_diversion(
        self,
        mac: str,
        dn: str,
        token: str,
        audit_context: RequestAuditContext,
    ) -> DisableResult:
        try:
            device_context = self._phone_manager_client.validate_device_context(mac, dn, token)
            current_state = self._cucm_axl_client.get_line(
                device_context.dn,
                self._route_partition_name,
            )
        except (PhoneManagerUnavailable, PhoneManagerValidationError, CucmAxlError) as exc:
            self._log_unavailable(
                audit_context,
                mac,
                dn,
                action="disable",
                entered_destination=None,
                error_summary=str(exc),
            )
            raise DiversionServiceUnavailable(str(exc)) from exc

        if not current_state.enabled:
            self._store_cache(device_context, current_state)
            log_audit(
                request_id=audit_context.request_id,
                source_ip=audit_context.source_ip,
                mac=device_context.mac,
                dn=device_context.dn,
                model=device_context.model,
                action="disable",
                entered_destination=None,
                normalized_destination=None,
                old_cfa_destination=None,
                new_cfa_destination=None,
                result="already-disabled",
                error_summary=None,
                axl_operation="getLine",
            )
            return DisableResult(
                device_context=device_context,
                previous_destination=None,
                resulting_state=current_state,
                dry_run=False,
                already_disabled=True,
            )

        if self._dry_run:
            simulated_state = replace(
                current_state,
                destination=None,
                forward_to_voice_mail=False,
            )
            log_audit(
                request_id=audit_context.request_id,
                source_ip=audit_context.source_ip,
                mac=device_context.mac,
                dn=device_context.dn,
                model=device_context.model,
                action="disable",
                entered_destination=None,
                normalized_destination=None,
                old_cfa_destination=current_state.destination,
                new_cfa_destination=None,
                result="dry-run",
                error_summary=None,
                axl_operation="getLine",
            )
            return DisableResult(
                device_context=device_context,
                previous_destination=current_state.destination,
                resulting_state=simulated_state,
                dry_run=True,
                already_disabled=False,
            )

        try:
            self._cucm_axl_client.update_call_forward_all(
                device_context.dn,
                self._route_partition_name,
                current_state=current_state,
                destination=None,
            )
            axl_operation = "updateLine"
            if self._apply_line_after_update and self._cucm_axl_client.supports_apply_line():
                self._cucm_axl_client.apply_line(device_context.dn, self._route_partition_name)
                axl_operation = "updateLine/applyLine"
            confirmed_state = self._cucm_axl_client.get_line(
                device_context.dn,
                self._route_partition_name,
            )
        except CucmAxlError as exc:
            self._log_unavailable(
                audit_context,
                device_context.mac,
                device_context.dn,
                action="disable",
                entered_destination=None,
                error_summary=str(exc),
            )
            raise DiversionServiceUnavailable(str(exc)) from exc

        if confirmed_state.destination:
            self._log_unavailable(
                audit_context,
                device_context.mac,
                device_context.dn,
                action="disable",
                entered_destination=None,
                error_summary="CUCM confirmation mismatch after disabling call diversion",
            )
            raise DiversionServiceUnavailable(
                "CUCM confirmation mismatch after disabling call diversion"
            )

        self._store_cache(device_context, confirmed_state)
        log_audit(
            request_id=audit_context.request_id,
            source_ip=audit_context.source_ip,
            mac=device_context.mac,
            dn=device_context.dn,
            model=device_context.model,
            action="disable",
            entered_destination=None,
            normalized_destination=None,
            old_cfa_destination=current_state.destination,
            new_cfa_destination=confirmed_state.destination,
            result="success",
            error_summary=None,
            axl_operation=axl_operation + "/getLine",
        )
        return DisableResult(
            device_context=device_context,
            previous_destination=current_state.destination,
            resulting_state=confirmed_state,
            dry_run=False,
            already_disabled=False,
        )

    def _get_line_state(
        self,
        device_context: DeviceContext,
        *,
        bypass_cache: bool,
    ) -> CallForwardAllState:
        cache_key = build_cache_key(
            device_context.mac,
            device_context.dn,
            self._route_partition_name,
        )
        if bypass_cache:
            self._cfa_cache.invalidate(cache_key)
        else:
            cached = self._cfa_cache.get(cache_key)
            if cached is not None:
                return cached

        line_state = self._cucm_axl_client.get_line(
            device_context.dn,
            self._route_partition_name,
        )
        self._cfa_cache.set(cache_key, line_state)
        return line_state

    def _store_cache(self, device_context: DeviceContext, state: CallForwardAllState) -> None:
        cache_key = build_cache_key(
            device_context.mac,
            device_context.dn,
            self._route_partition_name,
        )
        self._cfa_cache.set(cache_key, state)

    def _validate_destination(self, entered_destination: str) -> None:
        stripped = entered_destination.strip()
        digit_count = len(re.sub(r"\D", "", stripped))
        if not stripped or digit_count < 5 or digit_count > 20:
            raise DiversionInvalidDestination("Invalid destination")

    @staticmethod
    def _log_invalid_destination(
        audit_context: RequestAuditContext,
        mac: str,
        dn: str,
        entered_destination: str,
        error_summary: str,
    ) -> None:
        log_audit(
            request_id=audit_context.request_id,
            source_ip=audit_context.source_ip,
            mac=mac,
            dn=dn,
            model=None,
            action="enable",
            entered_destination=entered_destination,
            normalized_destination=None,
            old_cfa_destination=None,
            new_cfa_destination=None,
            result="error",
            error_summary=error_summary,
            axl_operation="normalize-number",
        )

    @staticmethod
    def _log_unavailable(
        audit_context: RequestAuditContext,
        mac: str,
        dn: str,
        *,
        action: str,
        entered_destination: str | None,
        error_summary: str,
    ) -> None:
        log_audit(
            request_id=audit_context.request_id,
            source_ip=audit_context.source_ip,
            mac=mac,
            dn=dn,
            model=None,
            action=action,
            entered_destination=entered_destination,
            normalized_destination=None,
            old_cfa_destination=None,
            new_cfa_destination=None,
            result="error",
            error_summary=error_summary,
            axl_operation="getLine/updateLine/applyLine",
        )


_service_instance: CallDiversionService | None = None


def get_diversion_service() -> CallDiversionService:
    global _service_instance
    if _service_instance is None:
        _service_instance = CallDiversionService(
            phone_manager_client=PhoneManagerClient(
                device_context_url=settings.PHONE_MANAGER_DEVICE_CONTEXT_URL,
                normalize_number_url=settings.PHONE_MANAGER_NORMALIZE_NUMBER_URL,
                timeout_seconds=settings.PHONE_MANAGER_TIMEOUT_SECONDS,
            ),
            cucm_axl_client=CucmAxlClient(
                wsdl_path=settings.AXL_WSDL_PATH,
                host=settings.CUCM_AXL_HOST,
                port=settings.CUCM_AXL_PORT,
                username=settings.CUCM_AXL_USERNAME,
                password=settings.CUCM_AXL_PASSWORD,
                verify_tls=settings.CUCM_AXL_VERIFY_TLS,
                timeout_seconds=settings.CUCM_AXL_TIMEOUT_SECONDS,
            ),
            route_partition_name=settings.CUCM_ROUTE_PARTITION,
            cfa_cache=InMemoryCfaCache(settings.CFA_CACHE_TTL_SECONDS),
            dry_run=settings.DRY_RUN,
            apply_line_after_update=settings.CUCM_APPLY_LINE_AFTER_UPDATE,
        )
    return _service_instance
