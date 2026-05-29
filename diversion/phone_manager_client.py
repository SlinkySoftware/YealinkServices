from __future__ import annotations

from dataclasses import dataclass

import requests


SUPPORTED_MODEL = "SIP-T33G"


class PhoneManagerError(RuntimeError):
    pass


class PhoneManagerUnavailable(PhoneManagerError):
    pass


class PhoneManagerValidationError(PhoneManagerError):
    pass


class InvalidDestinationError(PhoneManagerError):
    pass


@dataclass(frozen=True)
class DeviceContext:
    mac: str
    model: str
    dn: str
    sip_username: str | None
    line_count: int | None
    device_name: str | None
    site: str | None
    dial_plan_id: str | None
    message: str | None


class PhoneManagerClient:
    def __init__(
        self,
        device_context_url: str,
        normalize_number_url: str,
        timeout_seconds: int,
        session: requests.Session | None = None,
    ) -> None:
        self._device_context_url = device_context_url
        self._normalize_number_url = normalize_number_url
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def validate_device_context(self, mac: str, dn: str, token: str) -> DeviceContext:
        try:
            response = self._session.get(
                self._device_context_url,
                params={"mac": mac, "dn": dn, "token": token},
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise PhoneManagerUnavailable("Phone Manager device context request failed") from exc

        if response.status_code in {403, 404}:
            raise PhoneManagerValidationError("Invalid device context")
        if response.status_code != 200:
            raise PhoneManagerUnavailable(
                f"Unexpected Phone Manager status code {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PhoneManagerUnavailable("Phone Manager returned invalid JSON") from exc

        if payload.get("valid") is not True:
            raise PhoneManagerValidationError(payload.get("message", "Invalid device context"))

        model = str(payload.get("model", "")).strip().upper()
        if model != SUPPORTED_MODEL:
            raise PhoneManagerValidationError("Unsupported handset model")

        line_count = payload.get("line_count")
        if line_count not in {None, 1}:
            raise PhoneManagerValidationError("Multiple handset lines are not supported")

        normalized_mac = str(payload.get("mac", "")).strip().upper()
        normalized_dn = str(payload.get("dn", "")).strip()
        if not normalized_mac or not normalized_dn:
            raise PhoneManagerUnavailable("Phone Manager returned incomplete device context")

        return DeviceContext(
            mac=normalized_mac,
            model=model,
            dn=normalized_dn,
            sip_username=self._optional_string(payload.get("sip_username")),
            line_count=line_count,
            device_name=self._optional_string(payload.get("device_name")),
            site=self._optional_string(payload.get("site")),
            dial_plan_id=self._optional_string(payload.get("dial_plan_id")),
            message=self._optional_string(payload.get("message")),
        )

    def normalize_number(
        self,
        mac: str,
        dn: str,
        token: str,
        entered_destination: str,
    ) -> str:
        try:
            response = self._session.post(
                self._normalize_number_url,
                json={
                    "mac": mac,
                    "dn": dn,
                    "token": token,
                    "entered_destination": entered_destination,
                },
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise PhoneManagerUnavailable("Phone Manager normalize-number request failed") from exc

        if response.status_code == 400:
            raise InvalidDestinationError("Invalid destination")
        if response.status_code in {403, 404}:
            raise PhoneManagerValidationError("Normalize-number request rejected")
        if response.status_code != 200:
            raise PhoneManagerUnavailable(
                f"Unexpected Phone Manager status code {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PhoneManagerUnavailable("Phone Manager returned invalid JSON") from exc

        normalized_destination = self._optional_string(payload.get("normalized_destination"))
        if not normalized_destination:
            raise InvalidDestinationError("Missing normalized destination")

        return normalized_destination

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
