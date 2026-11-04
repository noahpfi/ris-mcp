"""unauthenticated extraction/amplification cases, no network; both found probing live endpoint"""
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
    """span counts numbers; suffixes are real units, not extra numbers"""
    result = await server.get_paragraph(law="ABGB", paragraph=start, to_paragraph=end)
    assert "limit is" not in result


async def test_result_fan_out_is_capped_when_suffixes_multiply_the_range(abgb):
    """§§ 1-5 = 5 numbers but 25 documents, 5a-5e past the end; cap must not depend on RIS answer"""
    result = await server.get_paragraph(law="ABGB", paragraph="1", to_paragraph="5")
    assert len(abgb.fetched) == server.MAX_RANGE
    assert f"25 documents match § 1 to § 5; showing the first {server.MAX_RANGE}" in result


@pytest.mark.parametrize("status", [400, 500, 503])
async def test_upstream_errors_do_not_leak_the_request_url(monkeypatch, status):
    """httpx puts full query string in exception; FastMCP would relay it"""
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
