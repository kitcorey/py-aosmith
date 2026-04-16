"""Baseline tests for existing py-aosmith client functionality."""

import pytest
from unittest.mock import AsyncMock

from py_aosmith.client import (
    _REDACTED,
    _redact_sensitive,
    build_passcode,
    device_is_compatible,
    map_mode_str_to_operation_mode_type,
    parse_hot_water_status,
)
from py_aosmith.exceptions import AOSmithUnknownException
from py_aosmith.models import OperationMode

from tests.conftest import make_response, STATUS_OK_RESPONSE, STATUS_FALSE_RESPONSE


class TestBuildPasscode:
    def test_encodes_credentials(self):
        result = build_passcode("user@example.com", "pass123")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_different_inputs_produce_different_outputs(self):
        a = build_passcode("a@example.com", "pass1")
        b = build_passcode("b@example.com", "pass2")
        assert a != b


class TestDeviceIsCompatible:
    def test_heat_pump(self):
        assert device_is_compatible({"data": {"__typename": "HeatPump"}}) is True

    def test_next_gen_heat_pump(self):
        assert device_is_compatible({"data": {"__typename": "NextGenHeatPump"}}) is True

    def test_re3_connected(self):
        assert device_is_compatible({"data": {"__typename": "RE3Connected"}}) is True

    def test_re3_premium(self):
        assert device_is_compatible({"data": {"__typename": "RE3Premium"}}) is True

    def test_unknown_type(self):
        assert device_is_compatible({"data": {"__typename": "SomeOtherType"}}) is False

    def test_missing_typename(self):
        assert device_is_compatible({"data": {}}) is False

    def test_missing_data(self):
        assert device_is_compatible({}) is False


class TestMapModeStr:
    def test_hybrid(self):
        assert map_mode_str_to_operation_mode_type("HYBRID") == OperationMode.HYBRID

    def test_heat_pump(self):
        assert map_mode_str_to_operation_mode_type("HEAT_PUMP") == OperationMode.HEAT_PUMP

    def test_efficiency(self):
        assert map_mode_str_to_operation_mode_type("EFFICIENCY") == OperationMode.HEAT_PUMP

    def test_electric(self):
        assert map_mode_str_to_operation_mode_type("ELECTRIC") == OperationMode.ELECTRIC

    def test_standard(self):
        assert map_mode_str_to_operation_mode_type("STANDARD") == OperationMode.ELECTRIC

    def test_vacation(self):
        assert map_mode_str_to_operation_mode_type("VACATION") == OperationMode.VACATION

    def test_guest(self):
        assert map_mode_str_to_operation_mode_type("GUEST") == OperationMode.GUEST

    def test_unknown_raises(self):
        with pytest.raises(AOSmithUnknownException, match="Unknown mode"):
            map_mode_str_to_operation_mode_type("TURBO")


class TestParseHotWaterStatus:
    def test_none(self):
        assert parse_hot_water_status(None) is None

    def test_low(self):
        assert parse_hot_water_status("LOW") == 0

    def test_medium(self):
        assert parse_hot_water_status("MEDIUM") == 50

    def test_high(self):
        assert parse_hot_water_status("HIGH") == 100

    def test_case_insensitive(self):
        assert parse_hot_water_status("low") == 0
        assert parse_hot_water_status("Medium") == 50

    def test_int_inverted(self):
        assert parse_hot_water_status(0) == 100
        assert parse_hot_water_status(100) == 0
        assert parse_hot_water_status(30) == 70

    def test_unknown_string_raises(self):
        with pytest.raises(AOSmithUnknownException, match="Unknown hot water status"):
            parse_hot_water_status("UNKNOWN")


class TestRedactSensitive:
    def test_redacts_passcode(self):
        assert _redact_sensitive({"passcode": "secret"}) == {"passcode": _REDACTED}

    def test_redacts_tokens(self):
        result = _redact_sensitive({
            "accessToken": "a",
            "idToken": "b",
            "refreshToken": "c",
        })
        assert result == {
            "accessToken": _REDACTED,
            "idToken": _REDACTED,
            "refreshToken": _REDACTED,
        }

    def test_redacts_password(self):
        assert _redact_sensitive({"password": "hunter2"}) == {"password": _REDACTED}

    def test_preserves_non_sensitive(self):
        data = {"forceUpdate": True, "junctionId": "abc123"}
        assert _redact_sensitive(data) == data

    def test_recursive_dict(self):
        result = _redact_sensitive({
            "data": {"login": {"user": {"tokens": {"accessToken": "x", "safe": 1}}}}
        })
        assert result == {
            "data": {"login": {"user": {"tokens": {"accessToken": _REDACTED, "safe": 1}}}}
        }

    def test_list_of_dicts(self):
        result = _redact_sensitive([{"passcode": "x"}, {"junctionId": "y"}])
        assert result == [{"passcode": _REDACTED}, {"junctionId": "y"}]

    def test_nested_list(self):
        result = _redact_sensitive({"items": [{"accessToken": "a"}, {"other": "b"}]})
        assert result == {"items": [{"accessToken": _REDACTED}, {"other": "b"}]}

    def test_primitives_passthrough(self):
        assert _redact_sensitive("hello") == "hello"
        assert _redact_sensitive(42) == 42
        assert _redact_sensitive(None) is None
        assert _redact_sensitive(True) is True

    def test_empty_dict(self):
        assert _redact_sensitive({}) == {}

    def test_empty_list(self):
        assert _redact_sensitive([]) == []

    def test_does_not_mutate_input(self):
        original = {"passcode": "secret", "junctionId": "abc"}
        _redact_sensitive(original)
        assert original == {"passcode": "secret", "junctionId": "abc"}


class TestIsEverythingOkay:
    async def test_returns_true(self, client, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(200, STATUS_OK_RESPONSE))
        result = await client.is_everything_okay()
        assert result is True

    async def test_returns_false(self, client, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(200, STATUS_FALSE_RESPONSE))
        result = await client.is_everything_okay()
        assert result is False
