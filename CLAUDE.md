# py-aosmith

Async Python client library for A. O. Smith iCOMM water heaters. Used by the Home Assistant `aosmith` integration.

## Build / Test

- `pyproject.toml` is Poetry-shaped (`[tool.poetry]`), but both `poetry.lock` and `uv.lock` are present — prefer `uv` (`uv sync`, `uv run pytest`).
- Test suite lives in `tests/` (pytest + pytest-asyncio, mocking via stdlib `unittest.mock.AsyncMock` patched onto the aiohttp session). No live cloud calls.
- Python 3.10+, depends on `aiohttp` and `tenacity`.

## Architecture

All API calls are GraphQL POSTs to a single endpoint, routed through one private method:

- `client.py` — `AOSmithAPIClient` class, all HTTP logic
  - `__send_graphql_query()` — the only method that makes HTTP requests. Wrapped by a tenacity retry decorator (6 attempts × 10s, retries on `AOSmithUnknownException`). Inner self-heal logic:
    - HTTP 401 → `__login()`, recurse once with `retrying_after_login=True`; second 401 raises `Received status code 401 after logging in`
    - HTTP 400 (when `login_required` and a token is held) → same re-login path; second 400 raises `Received status code 400 after logging in`
    - HTTP 502 / 503 / 504 → `_rotate_base_url()` and `continue` the inner loop until base URLs are exhausted
    - `aiohttp.ClientError` / `asyncio.TimeoutError` → `_rotate_base_url()`
  - `__login()` — authenticates via GraphQL, stores token on the instance.
  - Public methods (`get_devices`, `get_energy_use_data`, `update_setpoint`, `update_mode`, etc.) all delegate to `__send_graphql_query`.
- `queries.py` — GraphQL query strings.
- `models.py` — frozen dataclasses for API responses.
- `exceptions.py` — custom exception types.

Note: `__get_device_by_junction_id` (raises `Device not found`) and `map_mode_str_to_operation_mode_type` (raises `Unknown mode`) both raise `AOSmithUnknownException` and rely on tenacity's outer retry to self-heal — don't add per-request retry logic for them.

## Deploy

This fork is consumed by a Home Assistant deployment via two Dockerfiles:

- A production HA Dockerfile (separate repo) pins via `pip install git+https://github.com/kitcorey/py-aosmith.git@<tag>`. Bump the tag after release, then rebuild HA image + restart container.
- `Dockerfile.ha` in this repo (test harness) — `COPY` + `pip install` from the local working tree, no tag. Picks up whatever is currently checked out.
- Both Dockerfiles also `sed`-patch `REGULAR_INTERVAL` in the built-in `aosmith` integration to bump polling 30s → 300s. Don't drop that patch.
- Do NOT push to upstream `bdr99/py-aosmith` — this is a hard-fork deploy path.

## Upstream context

- Fork origin: `git@github.com:kitcorey/py-aosmith.git`
- Upstream: `https://github.com/bdr99/py-aosmith`
- HA core integration: `homeassistant/components/aosmith/coordinator.py` translates `AOSmithUnknownException` → `UpdateFailed` and `AOSmithInvalidCredentialsException` → `ConfigEntryAuthFailed`. No retry logic there; recovery has to live in the client.
