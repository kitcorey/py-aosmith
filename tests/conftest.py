"""Shared test fixtures for py-aosmith tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from tenacity import stop_after_attempt, wait_fixed

from py_aosmith.client import AOSmithAPIClient


STATUS_OK_RESPONSE = {"data": {"status": {"isEverythingOkay": True}}}
STATUS_FALSE_RESPONSE = {"data": {"status": {"isEverythingOkay": False}}}


def make_response(status: int, json_data: dict | None = None):
    """Create a mock aiohttp response with the given status and JSON body."""
    response = AsyncMock()
    response.status = status
    body = json_data or {}
    response.text = AsyncMock(return_value=json.dumps(body))
    response.json = AsyncMock(return_value=body)
    return response


def _disable_tenacity(client: AOSmithAPIClient):
    """Disable tenacity retry delays and limit to 1 attempt for testing."""
    retry_obj = client._AOSmithAPIClient__send_graphql_query.retry
    retry_obj.wait = wait_fixed(0)
    retry_obj.stop = stop_after_attempt(1)


@pytest.fixture
def mock_session():
    session = MagicMock(spec=aiohttp.ClientSession)
    session.request = AsyncMock()
    return session


@pytest.fixture
def client(mock_session):
    c = AOSmithAPIClient("test@test.com", "fake", session=mock_session)
    _disable_tenacity(c)
    return c


@pytest.fixture
def client_single_url(mock_session):
    c = AOSmithAPIClient("test@test.com", "fake", session=mock_session, base_url="https://r2.wh8.co")
    _disable_tenacity(c)
    return c
