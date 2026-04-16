"""Tests for credential redaction in debug logs."""

import logging
from unittest.mock import AsyncMock

import pytest

from py_aosmith.client import _REDACTED, build_passcode

from tests.conftest import make_response, STATUS_OK_RESPONSE


LOGIN_SUCCESS_RESPONSE = {
    "data": {
        "login": {
            "user": {
                "tokens": {
                    "accessToken": "secret-access-token-xyz",
                    "idToken": "secret-id-token-xyz",
                    "refreshToken": "secret-refresh-token-xyz",
                }
            }
        }
    }
}


class TestLoggingRedaction:
    async def test_login_passcode_not_logged(self, client, mock_session, caplog):
        """The passcode in login request variables must not appear in logs."""
        email = "user@example.com"
        password = "supersecretpassword"
        client.email = email
        client.password = password
        expected_passcode = build_passcode(email, password)

        # Force a login by marking login_required and no token
        mock_session.request = AsyncMock(side_effect=[
            make_response(200, LOGIN_SUCCESS_RESPONSE),
            make_response(200, STATUS_OK_RESPONSE),
        ])

        with caplog.at_level(logging.DEBUG, logger="py_aosmith.client"):
            # is_everything_okay has login_required=False, so we need a login-required call
            # Manually trigger login via the private method
            await client._AOSmithAPIClient__login()

        all_logs = "\n".join(record.getMessage() for record in caplog.records)

        # The raw passcode must not appear anywhere in the logs
        assert expected_passcode not in all_logs
        # The plaintext password must not appear either
        assert password not in all_logs
        # But we should see the redaction marker
        assert _REDACTED in all_logs

    async def test_login_response_tokens_not_logged(self, client, mock_session, caplog):
        """Access/id/refresh tokens in the login response body must not appear in logs."""
        mock_session.request = AsyncMock(return_value=make_response(200, LOGIN_SUCCESS_RESPONSE))

        with caplog.at_level(logging.DEBUG, logger="py_aosmith.client"):
            await client._AOSmithAPIClient__login()

        all_logs = "\n".join(record.getMessage() for record in caplog.records)

        # Raw token values must not leak
        assert "secret-access-token-xyz" not in all_logs
        assert "secret-id-token-xyz" not in all_logs
        assert "secret-refresh-token-xyz" not in all_logs
        # Key names should still appear (redacted), so the log structure is visible
        assert "accessToken" in all_logs
        assert _REDACTED in all_logs

    async def test_non_login_queries_not_redacted(self, client, mock_session, caplog):
        """Non-sensitive variables and response fields should NOT be redacted."""
        mock_session.request = AsyncMock(return_value=make_response(200, STATUS_OK_RESPONSE))

        with caplog.at_level(logging.DEBUG, logger="py_aosmith.client"):
            await client.is_everything_okay()

        all_logs = "\n".join(record.getMessage() for record in caplog.records)

        # isEverythingOkay value True should be visible in logs (not redacted)
        assert "isEverythingOkay" in all_logs
        # No redaction marker should appear for non-sensitive data
        assert _REDACTED not in all_logs

    async def test_non_json_response_falls_back_safely(self, client, mock_session, caplog):
        """If response body is not valid JSON, the fallback log path should be used."""
        response = make_response(200, STATUS_OK_RESPONSE)
        response.text = AsyncMock(return_value="not valid json {{{")

        mock_session.request = AsyncMock(return_value=response)

        with caplog.at_level(logging.DEBUG, logger="py_aosmith.client"):
            # This will fail later at response.json() but the text log happens first
            try:
                await client.is_everything_okay()
            except Exception:
                pass

        all_logs = "\n".join(record.getMessage() for record in caplog.records)
        # Should log a non-JSON marker, not the raw text
        assert "non-JSON" in all_logs
        assert "not valid json" not in all_logs
