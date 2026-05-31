from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from .cucm_axl_client import available_axl_schema_versions
from .logging_utils import new_request_id, source_ip_from_request
from .services import (
    DiversionInvalidDestination,
    DiversionServiceUnavailable,
    RequestAuditContext,
    get_diversion_service,
)
from .yealink_xml import (
    HandsetRequestError,
    build_screen_context,
    get_query_param,
    numeric_destination_value,
    parse_handset_request,
    render_xml_template,
)


def status_view(request: HttpRequest) -> HttpResponse:
    return _render_status(request, bypass_cache=False, action="status")


def enable_view(request: HttpRequest) -> HttpResponse:
    try:
        params = parse_handset_request(request)
    except HandsetRequestError:
        return _render_unavailable(None)

    try:
        status = get_diversion_service().get_status(
            params.mac,
            params.dn,
            params.token,
            _audit_context(request),
            bypass_cache=False,
            action="enable-form",
        )
    except DiversionServiceUnavailable:
        return _render_unavailable(params)

    return render_xml_template(
        "yealink/input_destination.xml",
        build_screen_context(
            params,
            dn=status.device_context.dn,
            default_destination=numeric_destination_value(status.line_state.destination),
        ),
    )


def set_view(request: HttpRequest) -> HttpResponse:
    try:
        params = parse_handset_request(request)
    except HandsetRequestError:
        return _render_unavailable(None)

    destination = get_query_param(request, "destination")
    if not destination:
        return _render_invalid_destination(params)

    try:
        result = get_diversion_service().enable_diversion(
            params.mac,
            params.dn,
            params.token,
            destination,
            _audit_context(request),
        )
    except DiversionInvalidDestination:
        return _render_invalid_destination(params)
    except DiversionServiceUnavailable:
        return _render_unavailable(params)

    return render_xml_template(
        "yealink/success_enabled.xml",
        build_screen_context(
            params,
            dn=result.device_context.dn,
            normalized_destination=result.normalized_destination,
        ),
    )


def disable_view(request: HttpRequest) -> HttpResponse:
    try:
        params = parse_handset_request(request)
    except HandsetRequestError:
        return _render_unavailable(None)

    try:
        result = get_diversion_service().disable_diversion(
            params.mac,
            params.dn,
            params.token,
            _audit_context(request),
        )
    except DiversionServiceUnavailable:
        return _render_unavailable(params)

    if result.already_disabled:
        return render_xml_template(
            "yealink/status_off.xml",
            build_screen_context(
                params,
                dn=result.device_context.dn,
            ),
        )

    return render_xml_template(
        "yealink/success_disabled.xml",
        build_screen_context(
            params,
            dn=result.device_context.dn,
        ),
    )


def refresh_view(request: HttpRequest) -> HttpResponse:
    return _render_status(request, bypass_cache=True, action="refresh")


def health_view(request: HttpRequest) -> JsonResponse:
    available_wsdl_versions = available_axl_schema_versions(settings.AXL_WSDL_ROOT)
    return JsonResponse(
        {
            "status": "ok",
            "service": "yealinkService",
            "wsdl_present": bool(available_wsdl_versions),
            "wsdl_versions": available_wsdl_versions,
            "base_url": settings.PHONE_SERVICES_BASE_URL,
            "root_mount_enabled": settings.PHONE_SERVICES_ENABLE_ROOT_MOUNT,
        }
    )


def _render_status(
    request: HttpRequest,
    *,
    bypass_cache: bool,
    action: str,
) -> HttpResponse:
    try:
        params = parse_handset_request(request)
    except HandsetRequestError:
        return _render_unavailable(None)

    try:
        status = get_diversion_service().get_status(
            params.mac,
            params.dn,
            params.token,
            _audit_context(request),
            bypass_cache=bypass_cache,
            action=action,
        )
    except DiversionServiceUnavailable:
        return _render_unavailable(params)

    template_name = "yealink/status_on.xml" if status.line_state.enabled else "yealink/status_off.xml"
    context = build_screen_context(
        params,
        dn=status.device_context.dn,
        diversion_destination=status.line_state.destination or "None",
        normalized_destination=status.line_state.destination or "",
    )
    return render_xml_template(template_name, context)


def _render_unavailable(params) -> HttpResponse:
    return render_xml_template(
        "yealink/error_unavailable.xml",
        build_screen_context(params),
    )


def _render_invalid_destination(params) -> HttpResponse:
    return render_xml_template(
        "yealink/error_invalid_destination.xml",
        build_screen_context(params),
    )


def _audit_context(request: HttpRequest) -> RequestAuditContext:
    return RequestAuditContext(
        request_id=new_request_id(),
        source_ip=source_ip_from_request(request),
    )

