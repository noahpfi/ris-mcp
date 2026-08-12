"""What an unauthenticated caller can extract or amplify. No network.

Both cases here were found by probing the live endpoint, not by reading code.
"""
from __future__ import annotations

import httpx
import pytest

from src import ris_client as rc
from src import server


def test_range_span_counts_plain_numbers():
    assert server._range_span("1", "20") == 20
    assert server._range_span("1295", "1295") == 1
    assert server._range_span("1", "") is None


def test_range_span_gives_up_on_suffixed_paragraphs():
    """§ 1295a is a real paragraph number; guessing a span from it would be wrong."""
    assert server._range_span("1295a", "1300b") == 6
    assert server._range_span("a", "b") is None


async def test_wide_range_is_refused_before_any_upstream_call(monkeypatch):
    calls = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(rc, "search_bundesrecht", spy)
    result = await server.get_paragraph(law="ABGB", paragraph="1", to_paragraph="1500")

    assert "limit is" in result
    assert not calls, "refused range still hit the RIS API"


async def test_range_within_the_limit_is_allowed(monkeypatch):
    async def search(**kwargs):
        return [], 0

    monkeypatch.setattr(rc, "search_bundesrecht", search)
    result = await server.get_paragraph(law="ABGB", paragraph="1", to_paragraph="20")
    assert "limit is" not in result


async def test_result_fan_out_is_capped_even_if_the_api_widens_the_range(monkeypatch):
    """RIS decides what a range matches; the cap cannot depend on its answer."""
    ref = {"Data": {"Metadaten": {
        "Technisch": {"ID": "d"}, "Allgemein": {"DokumentUrl": "u"},
        "Bundesrecht": {"Kurztitel": "ABGB", "BrKons": {"ArtikelParagraphAnlage": "§ 1"}}}}}
    fetched = []

    async def search(**kwargs):
        return [dict(ref) for _ in range(50)], 50

    async def fetch(r):
        fetched.append(r)
        return "<html><body><p>x</p></body></html>"

    monkeypatch.setattr(rc, "search_bundesrecht", search)
    monkeypatch.setattr(rc, "fetch_document_html", fetch)
    await server.get_paragraph(law="ABGB", paragraph="1", to_paragraph="5")

    assert len(fetched) <= server.MAX_RANGE


@pytest.mark.parametrize("status", [400, 500, 503])
async def test_upstream_errors_do_not_leak_the_request_url(monkeypatch, status):
    """httpx puts the full query string in its exception; FastMCP would relay it."""
    url = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht?Titel=secret&Applikation=BrKons"

    class FakeResponse:
        status_code = status
        is_error = True

        def json(self):  # pragma: no cover - never reached on an error status
            return {}

    class FakeClient:
        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(rc, "_client", lambda: FakeClient())
    monkeypatch.setattr(rc, "_cache", {})

    with pytest.raises(rc.RisApiError) as excinfo:
        await rc._get_json({"Titel": "secret", "Applikation": "BrKons"})

    message = str(excinfo.value)
    assert str(status) in message
    for leak in ("data.bka.gv.at", "Titel=", "secret", url):
        assert leak not in message


async def test_upstream_timeout_is_reported_without_internals(monkeypatch):
    class TimingOutClient:
        async def get(self, *args, **kwargs):
            raise httpx.TimeoutException("timed out", request=None)

    monkeypatch.setattr(rc, "_client", lambda: TimingOutClient())
    monkeypatch.setattr(rc, "_cache", {})
    monkeypatch.setattr(rc.asyncio, "sleep", lambda *_: _noop())

    with pytest.raises(rc.RisApiError, match="timed out"):
        await rc._get_json({"Titel": "x"})


async def _noop():
    return None
