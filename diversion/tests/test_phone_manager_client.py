from __future__ import annotations

from unittest.mock import Mock

import pytest

from diversion.phone_manager_client import (
    InvalidDestinationError,
    PhoneManagerClient,
    PhoneManagerUnavailable,
    PhoneManagerValidationError,
)


def build_client(session: Mock) -> PhoneManagerClient:
    return PhoneManagerClient(
        device_context_url="http://127.0.0.1:8000/internal/device-context/",
        normalize_number_url="http://127.0.0.1:8000/internal/normalize-number/",
        timeout_seconds=5,
        session=session,
    )


def test_validate_device_context_success() -> None:
    session = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {
        "valid": True,
        "mac": "805EC0ABCDEF",
        "model": "SIP-T33G",
        "dn": "+61288836500",
        "line_count": 1,
        "message": "OK",
    }
    session.get.return_value = response

    context = build_client(session).validate_device_context("80:5E:C0:AB:CD:EF", "+61288836500", "abcd1234")

    assert context.mac == "805EC0ABCDEF"
    assert context.dn == "+61288836500"
    assert context.model == "SIP-T33G"


def test_validate_device_context_invalid_model_rejected() -> None:
    session = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {
        "valid": True,
        "mac": "805EC0ABCDEF",
        "model": "SIP-T46U",
        "dn": "+61288836500",
        "line_count": 1,
    }
    session.get.return_value = response

    with pytest.raises(PhoneManagerValidationError):
        build_client(session).validate_device_context("805EC0ABCDEF", "+61288836500", "abcd1234")


def test_validate_device_context_unexpected_status_is_unavailable() -> None:
    session = Mock()
    session.get.return_value = Mock(status_code=502)

    with pytest.raises(PhoneManagerUnavailable):
        build_client(session).validate_device_context("805EC0ABCDEF", "+61288836500", "abcd1234")


def test_normalize_number_invalid_destination() -> None:
    session = Mock()
    session.post.return_value = Mock(status_code=400)

    with pytest.raises(InvalidDestinationError):
        build_client(session).normalize_number(
            "805EC0ABCDEF",
            "+61288836500",
            "abcd1234",
            "12",
        )
