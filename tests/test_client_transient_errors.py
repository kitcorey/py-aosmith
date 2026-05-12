"""Tests for transient server-side GraphQL error detection and endpoint rotation.

These cover the iCOMM Node backend leaking unhandled exceptions through GraphQL
`errors[]` (HTTP 200), e.g. `Cannot read properties of undefined (reading 'id')`.
Such payloads should trigger base-URL rotation, not a blind retry against the
same wedged endpoint.
"""

from unittest.mock import AsyncMock

import pytest

from py_aosmith.client import _is_transient_server_error
from py_aosmith.exceptions import AOSmithUnknownException

from tests.conftest import make_response, STATUS_OK_RESPONSE


LOGIN_RESPONSE = {
    "data": {"login": {"user": {"tokens": {"accessToken": "fresh-token"}}}}
}


class TestIsTransientServerErrorByCode:
    def test_internal_server_error_code(self):
        """Production payload from 2026-05-12: server emits this code on the JS leak."""
        assert _is_transient_server_error({
            "message": "anything",
            "extensions": {"code": "INTERNAL_SERVER_ERROR"},
        }) is True

    def test_internal_error_code(self):
        assert _is_transient_server_error({
            "message": "boom",
            "extensions": {"code": "INTERNAL_ERROR"},
        }) is True

    def test_service_unavailable_code(self):
        assert _is_transient_server_error({
            "message": "x",
            "extensions": {"code": "SERVICE_UNAVAILABLE"},
        }) is True

    def test_code_takes_precedence_over_benign_message(self):
        """If the server says INTERNAL_SERVER_ERROR, we trust it even if the message looks benign."""
        assert _is_transient_server_error({
            "message": "Device not found",
            "extensions": {"code": "INTERNAL_SERVER_ERROR"},
        }) is True

    def test_unknown_code_falls_back_to_message_match(self):
        assert _is_transient_server_error({
            "message": "Cannot read properties of undefined (reading 'id')",
            "extensions": {"code": "SOMETHING_ELSE"},
        }) is True

    def test_invalid_credentials_code_is_not_transient(self):
        """INVALID_CREDENTIALS should not be classified as transient — message doesn't match either."""
        assert _is_transient_server_error({
            "message": "Invalid email address or password",
            "extensions": {"code": "INVALID_CREDENTIALS"},
        }) is False

    def test_extensions_not_dict(self):
        """Defensive: extensions can be missing or non-dict on older payloads."""
        assert _is_transient_server_error({
            "message": "Cannot read properties of undefined",
            "extensions": "not a dict",
        }) is True  # falls back to message match

    def test_extensions_code_not_string(self):
        assert _is_transient_server_error({
            "message": "Device not found",
            "extensions": {"code": 500},
        }) is False  # falls back to message match, doesn't match → False


class TestIsTransientServerError:
    def test_cannot_read_properties_of_undefined(self):
        assert _is_transient_server_error(
            {"message": "Cannot read properties of undefined (reading 'id')"}
        ) is True

    def test_cannot_read_property_legacy(self):
        assert _is_transient_server_error(
            {"message": "Cannot read property 'foo' of undefined"}
        ) is True

    def test_internal_server_error(self):
        assert _is_transient_server_error(
            {"message": "Internal server error"}
        ) is True

    def test_is_not_a_function(self):
        assert _is_transient_server_error(
            {"message": "x.bar is not a function"}
        ) is True

    def test_is_not_defined(self):
        assert _is_transient_server_error(
            {"message": "foo is not defined"}
        ) is True

    def test_typeerror_prefix(self):
        assert _is_transient_server_error(
            {"message": "TypeError: cannot destructure property"}
        ) is True

    def test_referenceerror_prefix(self):
        assert _is_transient_server_error(
            {"message": "ReferenceError: bar is not defined"}
        ) is True

    def test_case_insensitive(self):
        assert _is_transient_server_error(
            {"message": "CANNOT READ PROPERTIES OF UNDEFINED"}
        ) is True

    def test_benign_business_error(self):
        assert _is_transient_server_error(
            {"message": "Device not found"}
        ) is False

    def test_invalid_credentials(self):
        assert _is_transient_server_error(
            {"message": "Invalid email address or password"}
        ) is False

    def test_missing_message(self):
        assert _is_transient_server_error({}) is False

    def test_empty_message(self):
        assert _is_transient_server_error({"message": ""}) is False

    def test_non_string_message(self):
        assert _is_transient_server_error({"message": 42}) is False


class TestRotateOnTransientServerError:
    async def test_failover_on_cannot_read_properties(self, client, mock_session):
        """The actual production outage signature: r1 returns the JS leak, r2 is healthy."""
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {
                "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
            }),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"
        second_call = mock_session.request.call_args_list[1]
        assert "r2.wh8.co" in second_call.kwargs["url"]

    async def test_failover_on_internal_server_error_code(self, client, mock_session):
        """Production payload from 2026-05-12: full GraphQL error envelope with extensions.code."""
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {
                "errors": [{
                    "message": "Cannot read properties of undefined (reading 'id')",
                    "locations": [{"line": 1, "column": 34}],
                    "path": ["login"],
                    "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                }],
                "data": {"login": None},
            }),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_failover_on_internal_server_error(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {"errors": [{"message": "Internal server error"}]}),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_failover_on_typeerror(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {
                "errors": [{"message": "TypeError: cannot destructure property 'x'"}]
            }),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_both_endpoints_transient_raises_with_prefix(self, client, mock_session):
        """When both endpoints return the same JS error, raise so tenacity retries."""
        bad_response = make_response(200, {
            "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
        })
        # Each call to mock_session.request returns a fresh response stub
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {
                "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
            }),
            make_response(200, {
                "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
            }),
        ])

        with pytest.raises(AOSmithUnknownException, match="Transient server error"):
            await client.is_everything_okay()

        # Both endpoints were attempted in order
        assert mock_session.request.call_count == 2
        urls = [c.kwargs["url"] for c in mock_session.request.call_args_list]
        assert "r1.wh8.co" in urls[0]
        assert "r2.wh8.co" in urls[1]

    async def test_benign_graphql_error_does_not_rotate(self, client, mock_session):
        """Real business errors (e.g. 'Device not found') stay put — rotating wouldn't help."""
        mock_session.request = AsyncMock(return_value=make_response(200, {
            "errors": [{"message": "Device not found"}]
        }))

        with pytest.raises(AOSmithUnknownException, match="Device not found"):
            await client.is_everything_okay()

        assert client._active_base_url == "https://r1.wh8.co"
        assert mock_session.request.call_count == 1

    async def test_transient_rotation_with_login_required(self, client, mock_session):
        """Authenticated path: rotation clears the token, so r2 forces a fresh login."""
        client.token = "stale-on-r1"

        mock_session.request = AsyncMock(side_effect=[
            # Initial call to r1 with stale token returns transient server bug
            make_response(200, {
                "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
            }),
            # After rotation: login on r2
            make_response(200, LOGIN_RESPONSE),
            # Authenticated request on r2 succeeds
            make_response(200, {"data": {"devices": []}}),
        ])

        result = await client.get_devices()

        assert result == []
        assert client._active_base_url == "https://r2.wh8.co"
        assert client.token == "fresh-token"
        assert mock_session.request.call_count == 3

    async def test_mixed_errors_with_transient_triggers_rotation(self, client, mock_session):
        """If any error in errors[] is transient, rotate — JS leaks often co-occur with other noise."""
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, {
                "errors": [
                    {"message": "Something contextual"},
                    {"message": "Cannot read properties of undefined (reading 'id')"},
                ]
            }),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_single_url_transient_raises(self, client_single_url, mock_session):
        """With only one base URL, a transient error has nowhere to rotate to."""
        mock_session.request = AsyncMock(return_value=make_response(200, {
            "errors": [{"message": "Cannot read properties of undefined (reading 'id')"}]
        }))

        with pytest.raises(AOSmithUnknownException, match="Transient server error"):
            await client_single_url.is_everything_okay()

        assert mock_session.request.call_count == 1


class TestNonJsonBody:
    async def test_non_json_200_rotates(self, client, mock_session):
        """An HTML error page returned with HTTP 200 (CDN/WAF) should rotate."""
        bad = AsyncMock()
        bad.status = 200
        bad.text = AsyncMock(return_value="<html>oops</html>")
        good = make_response(200, STATUS_OK_RESPONSE)

        mock_session.request = AsyncMock(side_effect=[bad, good])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_non_json_200_on_single_url_raises(self, client_single_url, mock_session):
        bad = AsyncMock()
        bad.status = 200
        bad.text = AsyncMock(return_value="not json")

        mock_session.request = AsyncMock(return_value=bad)

        with pytest.raises(AOSmithUnknownException, match="Invalid JSON in response"):
            await client_single_url.is_everything_okay()
