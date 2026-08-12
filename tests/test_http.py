"""HTTP transport: MCP handshake, health, Host validation, rate limiting.

Everything runs in-process against the real ASGI app. No RIS traffic: the one
tool exercised end-to-end is who_mentions, which only touches the local index.
"""
from __future__ import annotations

import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

from src import config, http_app, index
from src.server import mcp

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Host": "ris.test",
}
PROTOCOL_VERSION = "2025-06-18"


def _rpc(method: str, params: dict | None = None, request_id: int | None = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    return body


def _payload(response: httpx.Response) -> dict:
    """Read a result whether the server answered JSON or an SSE frame."""
    if response.headers["content-type"].startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise AssertionError(f"no data frame in {response.text!r}")
    return response.json()


@pytest.fixture(autouse=True)
def seeded_index():
    if index.DB_PATH.exists():
        index.DB_PATH.unlink()
    index._count_cache.clear()
    index.init_db()
    with index._connect() as conn:
        index._upsert(conn, "d1", "ABGB", "§ 1295", "https://ris/1295",
                      "1812-06-01", "Wer schuldhaft handelt, haftet nach § 1295 ABGB.")
    index._count_cache.clear()
    yield
    index._count_cache.clear()


@pytest.fixture
async def app():
    # Fresh session manager per test so each one gets its own rate-limit state
    # and its own task group; the manager refuses to run twice.
    mcp._session_manager = None
    built = http_app.build_app(mcp)
    async with LifespanManager(built):
        yield built


@pytest.fixture
def make_client(app):
    def _make(ip: str = "203.0.113.7") -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(ip, 51234)),
            base_url="http://ris.test",
        )

    return _make


@pytest.fixture
async def client(make_client):
    async with make_client() as c:
        yield c


@pytest.fixture
async def static_client(monkeypatch, tmp_path):
    """Same app, with a stand-in for the built landing page mounted."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>ris-mcp</title><h1>ris-mcp</h1>")
    monkeypatch.setattr(config, "STATIC_DIR", site)

    mcp._session_manager = None
    app = http_app.build_app(mcp)
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 51234)),
            base_url="http://ris.test",
        ) as c:
            yield c


async def _initialize(client: httpx.AsyncClient) -> None:
    response = await client.post("/mcp", headers=MCP_HEADERS, json=_rpc(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    ))
    assert response.status_code == 200, response.text
    assert _payload(response)["result"]["serverInfo"]["name"] == "ris-mcp"


async def test_initialize_handshake(client):
    await _initialize(client)


LIVE_API_TOOLS = {
    "search_law", "get_paragraph", "get_paragraph_at", "get_statute",
    "get_law_outline", "lookup_bgbl", "get_amendment_timeline",
}


async def test_tools_list_exposes_the_full_surface(client):
    await _initialize(client)
    response = await client.post("/mcp", headers=MCP_HEADERS, json=_rpc("tools/list", {}, 2))
    assert response.status_code == 200, response.text
    names = {tool["name"] for tool in _payload(response)["result"]["tools"]}
    assert names == LIVE_API_TOOLS | {"who_mentions"}


async def test_tool_call_reaches_the_local_index(client):
    await _initialize(client)
    response = await client.post("/mcp", headers=MCP_HEADERS, json=_rpc(
        "tools/call", {"name": "who_mentions", "arguments": {"reference": "§ 1295 ABGB"}}, 3,
    ))
    assert response.status_code == 200, response.text
    text = _payload(response)["result"]["content"][0]["text"]
    assert "ABGB" in text and "§ 1295" in text


async def test_tool_call_survives_fts_operators(client):
    """A hostile citation must come back as a normal empty result, not a 500."""
    await _initialize(client)
    response = await client.post("/mcp", headers=MCP_HEADERS, json=_rpc(
        "tools/call", {"name": "who_mentions", "arguments": {"reference": 'x" OR body:*'}}, 4,
    ))
    assert response.status_code == 200, response.text
    result = _payload(response)["result"]
    assert result.get("isError") is not True


async def test_stateless_needs_no_session_id(client):
    """Two independent initialize calls, no mcp-session-id carried between them."""
    await _initialize(client)
    await _initialize(client)


async def test_health_reports_index_state(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["index"] is True
    assert body["tools"] == len(LIVE_API_TOOLS) + 1
    assert body["docs"] == 1
    assert "last_crawl" in body


async def test_unknown_host_header_is_rejected(client):
    response = await client.post(
        "/mcp", headers={**MCP_HEADERS, "Host": "evil.example"}, json=_rpc("tools/list", {}, 5),
    )
    assert response.status_code == 421


async def test_rate_limit_returns_429_with_retry_after(client):
    await _initialize(client)
    statuses = []
    for i in range(config.RATE_LIMIT + 3):
        response = await client.post(
            "/mcp", headers=MCP_HEADERS, json=_rpc("tools/list", {}, 100 + i),
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            assert response.headers["Retry-After"] == str(config.RATE_WINDOW)
            assert response.json()["error"] == "rate_limited"
    assert 429 in statuses, f"limit {config.RATE_LIMIT} never tripped: {statuses}"


async def test_rate_limit_is_per_ip(client, make_client):
    """One noisy address must not lock everyone else out of the same server."""
    saturated = False
    for i in range(config.RATE_LIMIT + 5):
        response = await client.post(
            "/mcp", headers=MCP_HEADERS, json=_rpc("tools/list", {}, 200 + i),
        )
        saturated = saturated or response.status_code == 429
    assert saturated

    async with make_client("198.51.100.9") as other:
        response = await other.post(
            "/mcp", headers=MCP_HEADERS, json=_rpc("tools/list", {}, 250),
        )
        assert response.status_code == 200


async def test_health_is_exempt_from_the_rate_limit(client):
    for i in range(config.RATE_LIMIT + 5):
        assert (await client.get("/health")).status_code == 200


async def test_landing_page_is_served_from_the_same_origin(static_client):
    response = await static_client.get("/")
    assert response.status_code == 200
    assert "ris-mcp" in response.text
    assert response.headers["content-type"].startswith("text/html")


async def test_static_mount_does_not_shadow_the_mcp_endpoint(static_client):
    await _initialize(static_client)
    response = await static_client.post("/mcp", headers=MCP_HEADERS, json=_rpc("tools/list", {}, 2))
    assert response.status_code == 200, response.text
    assert _payload(response)["result"]["tools"]


async def test_static_mount_does_not_shadow_health(static_client):
    assert (await static_client.get("/health")).json()["status"] == "ok"


async def test_page_views_are_not_rate_limited(static_client):
    """The limiter guards the RIS upstream; a page load never reaches it."""
    for i in range(config.RATE_LIMIT + 10):
        assert (await static_client.get("/")).status_code == 200


async def test_missing_static_dir_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATIC_DIR", tmp_path / "never-built")
    mcp._session_manager = None
    with pytest.raises(ValueError, match="not a directory"):
        http_app.build_app(mcp)


async def test_forwarded_ip_header_identifies_the_caller(client):
    """cloudflared reports the real client; the socket peer is the tunnel."""
    for i in range(config.RATE_LIMIT + 3):
        response = await client.post(
            "/mcp",
            headers={**MCP_HEADERS, "CF-Connecting-IP": "192.0.2.55"},
            json=_rpc("tools/list", {}, 300 + i),
        )
        if response.status_code == 429:
            break
    else:
        pytest.fail("CF-Connecting-IP was not used as the bucket key")

    response = await client.post(
        "/mcp",
        headers={**MCP_HEADERS, "CF-Connecting-IP": "192.0.2.56"},
        json=_rpc("tools/list", {}, 400),
    )
    assert response.status_code == 200
