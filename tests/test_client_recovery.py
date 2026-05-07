"""Tests for stale-session recovery and 5xx failover behavior."""

from unittest.mock import AsyncMock

import pytest
from tenacity import stop_after_attempt, wait_fixed

from py_aosmith.client import AOSmithAPIClient
from py_aosmith.exceptions import AOSmithUnknownException

from tests.conftest import make_response, STATUS_OK_RESPONSE


LOGIN_RESPONSE = {
    "data": {"login": {"user": {"tokens": {"accessToken": "fresh-token"}}}}
}

DEVICES_RESPONSE = {"data": {"devices": []}}


class TestRecoverFrom400:
    async def test_400_then_relogin_then_200(self, client, mock_session):
        """Stale token causes 400; client re-logs in once and the retry succeeds."""
        client.token = "stale"

        mock_session.request = AsyncMock(side_effect=[
            make_response(400),
            make_response(200, LOGIN_RESPONSE),
            make_response(200, DEVICES_RESPONSE),
        ])

        result = await client.get_devices()

        assert result == []
        assert mock_session.request.call_count == 3
        assert client.token == "fresh-token"
        assert client._active_base_url == "https://r1.wh8.co"

    async def test_400_then_relogin_then_400(self, client, mock_session):
        """Second 400 after re-login is a hard failure with the 'after logging in' message."""
        client.token = "stale"

        mock_session.request = AsyncMock(side_effect=[
            make_response(400),
            make_response(200, LOGIN_RESPONSE),
            make_response(400),
        ])

        with pytest.raises(AOSmithUnknownException, match="Received status code 400 after logging in"):
            await client.get_devices()

        assert mock_session.request.call_count == 3

    async def test_400_with_login_not_required(self, client, mock_session):
        """A 400 on the unauthenticated path must not trigger re-login (guards login mutation against looping)."""
        assert client.token is None

        mock_session.request = AsyncMock(return_value=make_response(400))

        with pytest.raises(AOSmithUnknownException) as exc_info:
            await client.is_everything_okay()

        assert mock_session.request.call_count == 1
        assert str(exc_info.value) == "Received status code 400"

    async def test_400_cold_start_recovers(self, client, mock_session):
        """Cold start: token=None, login_required=True. Login → 400 → re-login → 200."""
        assert client.token is None

        mock_session.request = AsyncMock(side_effect=[
            make_response(200, LOGIN_RESPONSE),       # initial login (top-of-loop)
            make_response(400),                        # request fails with 400
            make_response(200, LOGIN_RESPONSE),       # re-login
            make_response(200, DEVICES_RESPONSE),     # retry succeeds
        ])

        result = await client.get_devices()

        assert result == []
        assert mock_session.request.call_count == 4
        assert client.token == "fresh-token"


class TestFailoverOn5xx:
    async def test_failover_on_502(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(502),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"
        second_call = mock_session.request.call_args_list[1]
        assert "r2.wh8.co" in second_call.kwargs["url"]

    async def test_failover_on_504(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(504),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_all_endpoints_fail_502(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(502),
            make_response(502),
        ])

        with pytest.raises(AOSmithUnknownException, match="Received status code 502"):
            await client.is_everything_okay()

    async def test_single_url_504_raises(self, client_single_url, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(504))

        with pytest.raises(AOSmithUnknownException, match="Received status code 504"):
            await client_single_url.is_everything_okay()


class TestTenacityWrapsAfterRecovery:
    async def test_tenacity_retries_unknown_exception(self, mock_session):
        """Outer tenacity must still retry AOSmithUnknownException after the new branches."""
        c = AOSmithAPIClient("test@test.com", "fake", session=mock_session)
        retry_obj = c._AOSmithAPIClient__send_graphql_query.retry
        retry_obj.wait = wait_fixed(0)
        retry_obj.stop = stop_after_attempt(3)

        # is_everything_okay uses login_required=False, so 400 never recurses —
        # each tenacity attempt is exactly one session.request call.
        mock_session.request = AsyncMock(return_value=make_response(400))

        with pytest.raises(AOSmithUnknownException, match="Received status code 400"):
            await c.is_everything_okay()

        # 3 attempts at the outer decorator level.
        assert mock_session.request.call_count == 3
