from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string


XML_CONTENT_TYPE = "application/xml; charset=utf-8"
ROUTE_PATHS = {
    "status": "",
    "enable": "enable/",
    "set": "set/",
    "disable": "disable/",
    "refresh": "refresh/",
}


class HandsetRequestError(ValueError):
    pass


@dataclass(frozen=True)
class HandsetRequestParams:
    mac: str
    dn: str
    token: str


def get_query_param(request: HttpRequest, primary_name: str, *aliases: str) -> str | None:
    for candidate in (primary_name, *aliases):
        value = request.GET.get(candidate)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def parse_handset_request(request: HttpRequest) -> HandsetRequestParams:
    mac = get_query_param(request, "mac")
    dn = get_query_param(request, "dn", "DN")
    token = get_query_param(request, "token")
    if not mac or not dn or not token:
        raise HandsetRequestError("Missing required handset request parameters")
    return HandsetRequestParams(mac=mac, dn=dn, token=token)


def build_screen_context(
    params: HandsetRequestParams | None = None,
    **extra_context: object,
) -> dict[str, object]:
    context: dict[str, object] = {
        "company_name": settings.PHONE_SERVICES_COMPANY_NAME,
        "branding_title": f"{settings.PHONE_SERVICES_COMPANY_NAME} Phone Services",
        "header_logo_url": settings.PHONE_SERVICES_HEADER_LOGO_URL,
        "fullscreen_logo_url": settings.PHONE_SERVICES_FULLSCREEN_LOGO_URL,
    }
    if params is not None:
        context.update(
            {
                "mac": params.mac,
                "dn": params.dn,
                "token": params.token,
                "status_url": build_absolute_service_url("status", params),
                "enable_url": build_absolute_service_url("enable", params),
                "set_url": build_absolute_service_url("set", params),
                "disable_url": build_absolute_service_url("disable", params),
                "refresh_url": build_absolute_service_url("refresh", params),
                "cancel_url": build_absolute_service_url("status", params),
                "exit_url": build_absolute_service_url("status", params),
            }
        )
    context.update(extra_context)
    return context


def build_absolute_service_url(
    route_name: str,
    params: HandsetRequestParams,
    **extra_query: object,
) -> str:
    base_url = settings.PHONE_SERVICES_BASE_URL.rstrip("/") + "/"
    route_path = ROUTE_PATHS[route_name]
    target = urljoin(base_url, route_path)
    query = {
        "mac": params.mac,
        "dn": params.dn,
        "token": params.token,
    }
    for key, value in extra_query.items():
        if value is not None:
            query[key] = value
    return f"{target}?{urlencode(query)}"


def render_xml_template(
    template_name: str,
    context: dict[str, object],
    *,
    status: int = 200,
) -> HttpResponse:
    xml = render_to_string(template_name, context)
    response = HttpResponse(xml, status=status, content_type=XML_CONTENT_TYPE)
    response["Cache-Control"] = "no-store, no-cache, max-age=0"
    return response
