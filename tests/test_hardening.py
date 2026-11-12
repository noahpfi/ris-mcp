"""Requests a caller could use to fan out upstream calls, refused before any request, and the handling of
HTTP failures. No network: transport failures come from httpx.MockTransport, not from copied RIS data."""
from __future__ import annotations

import httpx
import pytest

from src import ris_client as rc
from src import server


@pytest.mark.parametrize("start, end", [("1", "1500"), ("§ 1", "§ 21"), ("Art. 1", "30")])
async def test_wide_range_is_refused_before_any_upstream_call(no_upstream, start, end):
    result = await server.get_paragraph(law="ABGB", paragraph=start, to_paragraph=end)
    assert "limit is" in result


def _transport(monkeypatch, handler):
    """Routes RIS requests to handler and skips the backoff waits."""
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(rc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(record)))
    monkeypatch.setattr(rc, "_cache", {})
    monkeypatch.setattr(rc.asyncio, "sleep", lambda *_: _noop())
    return calls


@pytest.mark.parametrize("status", [429, 503])
async def test_throttling_is_retried_then_reported_with_attempts(monkeypatch, status):
    calls = _transport(monkeypatch, lambda request: httpx.Response(status, headers={"Retry-After": "30"}))
    with pytest.raises(rc.RisApiError) as excinfo:
        await rc._get_json({"Titel": "x"})
    message = str(excinfo.value)
    assert len(calls) == rc.ATTEMPTS
    assert f"RIS answered {status} " in message and f"{rc.ATTEMPTS} times" in message
    assert "Retry-After: 30" in message


async def test_throttling_that_clears_returns_the_answer(monkeypatch):
    answers = iter([httpx.Response(429), httpx.Response(200, json={"ok": True})])
    _transport(monkeypatch, lambda request: next(answers))
    assert await rc._get_json({"Titel": "x"}) == {"ok": True}


async def test_other_errors_raise_at_once_with_the_status(monkeypatch):
    calls = _transport(monkeypatch, lambda request: httpx.Response(400))
    with pytest.raises(rc.RisApiError, match="RIS answered 400 Bad Request"):
        await rc._get_json({"Titel": "secret"})
    assert len(calls) == 1


async def test_no_answer_is_reported_with_the_transport_error(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    _transport(monkeypatch, refuse)
    with pytest.raises(rc.RisApiError, match=f"gave no answer, ConnectError {rc.ATTEMPTS} times"):
        await rc._get_html("https://www.ris.bka.gv.at/doc.html")


async def test_body_that_is_not_json_is_quoted_with_its_url(monkeypatch):
    _transport(monkeypatch, lambda request: httpx.Response(200, text="<html>Wartung</html>"))
    with pytest.raises(rc.RisApiError) as excinfo:
        await rc._get_json({"Titel": "x"})
    assert "<html>Wartung</html>" in str(excinfo.value) and rc.BASE_URL in str(excinfo.value)


async def test_document_page_errors_raise_instead_of_returning_empty_text(monkeypatch):
    _transport(monkeypatch, lambda request: httpx.Response(429))
    with pytest.raises(rc.RisApiError):
        await rc._get_html("https://www.ris.bka.gv.at/doc.html")


async def _noop():
    return None
