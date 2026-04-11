"""Tests for endpoint failover behavior."""

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from py_aosmith.client import AOSmithAPIClient, API_BASE_URLS
from py_aosmith.exceptions import AOSmithUnknownException

from tests.conftest import make_response, STATUS_OK_RESPONSE


class TestConstructor:
    def test_default_base_urls(self, mock_session):
        client = AOSmithAPIClient("test@test.com", "fake", session=mock_session)
        assert client._base_urls == ["https://r1.wh8.co", "https://r2.wh8.co"]
        assert client._active_base_url == "https://r1.wh8.co"

    def test_custom_base_url(self, mock_session):
        client = AOSmithAPIClient("test@test.com", "fake", session=mock_session, base_url="https://r2.wh8.co")
        assert client._base_urls == ["https://r2.wh8.co"]
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_session_created_when_none(self):
        client = AOSmithAPIClient("test@test.com", "fake")
        assert isinstance(client.session, aiohttp.ClientSession)
        await client.session.close()

    def test_session_reused(self, mock_session):
        client = AOSmithAPIClient("test@test.com", "fake", session=mock_session)
        assert client.session is mock_session

    def test_default_urls_are_copy(self, mock_session):
        """Modifying client._base_urls should not affect the module constant."""
        client = AOSmithAPIClient("test@test.com", "fake", session=mock_session)
        client._base_urls.append("https://extra.example.com")
        assert len(API_BASE_URLS) == 2


class TestRotateBaseUrl:
    def test_rotate_to_next(self, client):
        assert client._active_base_url == "https://r1.wh8.co"
        client._rotate_base_url()
        assert client._active_base_url == "https://r2.wh8.co"

    def test_rotate_wraps_around(self, client):
        client._active_base_url = "https://r2.wh8.co"
        client._rotate_base_url()
        assert client._active_base_url == "https://r1.wh8.co"

    def test_rotate_clears_token(self, client):
        client.token = "some-token"
        client._rotate_base_url()
        assert client.token is None

    def test_rotate_single_url(self, client_single_url):
        assert client_single_url._active_base_url == "https://r2.wh8.co"
        client_single_url._rotate_base_url()
        assert client_single_url._active_base_url == "https://r2.wh8.co"


class TestFailover:
    async def test_failover_on_503(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(503),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"
        # Verify second call went to r2
        second_call = mock_session.request.call_args_list[1]
        assert "r2.wh8.co" in second_call.kwargs["url"]

    async def test_failover_on_timeout(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            asyncio.TimeoutError(),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_failover_on_connection_error(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            aiohttp.ClientError("Connection refused"),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        assert client._active_base_url == "https://r2.wh8.co"

    async def test_no_failover_on_400(self, client, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(400))

        with pytest.raises(AOSmithUnknownException, match="400"):
            await client.is_everything_okay()

        assert client._active_base_url == "https://r1.wh8.co"

    async def test_no_failover_on_graphql_error(self, client, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(200, {
            "errors": [{"message": "Something went wrong"}]
        }))

        with pytest.raises(AOSmithUnknownException, match="Something went wrong"):
            await client.is_everything_okay()

        assert client._active_base_url == "https://r1.wh8.co"

    async def test_all_endpoints_fail_503(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            make_response(503),
            make_response(503),
        ])

        with pytest.raises(AOSmithUnknownException, match="503"):
            await client.is_everything_okay()

    async def test_all_endpoints_fail_timeout(self, client, mock_session):
        mock_session.request = AsyncMock(side_effect=[
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
        ])

        with pytest.raises(AOSmithUnknownException, match="Request failed"):
            await client.is_everything_okay()

    async def test_single_url_503_raises(self, client_single_url, mock_session):
        mock_session.request = AsyncMock(return_value=make_response(503))

        with pytest.raises(AOSmithUnknownException, match="503"):
            await client_single_url.is_everything_okay()

    async def test_active_url_persists_after_failover(self, client, mock_session):
        # First call: r1 fails with 503, r2 succeeds
        mock_session.request = AsyncMock(side_effect=[
            make_response(503),
            make_response(200, STATUS_OK_RESPONSE),
        ])
        await client.is_everything_okay()
        assert client._active_base_url == "https://r2.wh8.co"

        # Second call: should go directly to r2
        mock_session.request = AsyncMock(return_value=make_response(200, STATUS_OK_RESPONSE))
        await client.is_everything_okay()

        call_url = mock_session.request.call_args.kwargs["url"]
        assert "r2.wh8.co" in call_url

    async def test_no_failover_on_401_triggers_relogin(self, client, mock_session):
        """401 should trigger re-login, not endpoint rotation."""
        client.token = "expired-token"

        # First request returns 401, login succeeds, retry returns 200
        mock_session.request = AsyncMock(side_effect=[
            make_response(401),
            # login call
            make_response(200, {"data": {"login": {"user": {"tokens": {"accessToken": "new-token"}}}}}),
            # retry after login
            make_response(200, STATUS_OK_RESPONSE),
        ])

        result = await client.is_everything_okay()

        assert result is True
        # URL should NOT have rotated
        assert client._active_base_url == "https://r1.wh8.co"
