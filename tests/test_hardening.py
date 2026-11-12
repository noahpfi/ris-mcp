"""Extraction and amplification an unauthenticated caller could trigger, both found by probing the live
endpoint. No network."""
from __future__ import annotations

import httpx
import pytest

from src import ris_client as rc
from src import server

from fake_ris import FakeRis, law


@pytest.fixture
def abgb(monkeypatch):
    suffixed = [f"§ {n}{s}" for n in range(1, 6) for s in ("", "a", "b", "c", "d", "e")]
    return FakeRis(law("10001622", "Allgemeines bürgerliches Gesetzbuch", "ABGB", *suffixed)).install(monkeypatch)


@pytest.mark.parametrize("start, end", [("1", "1500"), ("§ 1", "§ 21"), ("Art. 1", "30")])
async def test_wide_range_is_refused_before_any_upstream_call(abgb, start, end):
    result = await server.get_paragraph(law="ABGB", paragraph=start, to_paragraph=end)
    assert "limit is" in result
    assert not abgb.calls, "refused range still hit the RIS API"


@pytest.mark.parametrize("start, end", [("1", "20"), ("1295a", "1300b")])
async def test_range_within_the_limit_is_allowed(abgb, start, end):
    """The span counts numbers. Suffixed units such as 1295a do not add to it."""
    result = await server.get_paragraph(law="ABGB", paragraph=start, to_paragraph=end)
    assert "limit is" not in result


async def test_result_fan_out_is_capped_when_suffixes_multiply_the_range(abgb):
    """§§ 1 to 5 span five numbers but match 25 documents, since 5a to 5e sort past the end.
    The document cap must hold whatever RIS returns."""
    result = await server.get_paragraph(law="ABGB", paragraph="1", to_paragraph="5")
    assert len(abgb.fetched) == server.MAX_RANGE
    assert f"25 documents match § 1 to § 5; showing the first {server.MAX_RANGE}" in result


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


@pytest.mark.parametrize("status", [400, 500])
async def test_other_errors_raise_at_once_with_status_and_ris_message(monkeypatch, status):
    body = {"OgdSearchResult": {"Error": {"Message": "Eingabe ungültig"}}}
    calls = _transport(monkeypatch, lambda request: httpx.Response(status, json=body))
    with pytest.raises(rc.RisApiError) as excinfo:
        await rc._get_json({"Titel": "secret"})
    message = str(excinfo.value)
    assert len(calls) == 1
    assert str(status) in message and "Eingabe ungültig" in message
    assert "secret" not in message


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
