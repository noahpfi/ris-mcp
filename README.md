# ris-mcp

MCP server for Austrian federal law (Bundesrecht), backed by the public [RIS](https://www.ris.bka.gv.at/) OGD API v2.6.

## Tools

| Tool | Purpose | Hosted |
|---|---|---|
| `search_law` | Full-text search across Bundesrecht in force on a date, newest first | ✓ |
| `get_paragraph` | Fetch paragraphs, articles or annexes by number, or a range such as §§ 200–210 UGB, as in force on a date | ✓ |
| `get_statute` | Preamble, amendments and first paragraphs of a law | ✓ |
| `get_law_outline` | Every document of a law under its RIS label, with headings and sections | ✓ |
| `lookup_bgbl` | Text and metadata of a BGBl entry, e.g. `BGBl. I Nr. 50/2023` | ✓ |
| `get_amendment_timeline` | The amendments RIS lists for a law | ✓ |
| `who_mentions` | Reverse citation lookup, e.g. which provisions cite `§ 879` | self-host |

## Naming a law

Tools take a law by name, short title or abbreviation, such as `ABGB`, `EStG` or `Konsumentenschutzgesetz`. RIS searches its titles for the whole words given; the server adds `*` to words of three or more characters, so `Einkommensteuer` finds the Einkommensteuergesetz.

When exactly one hit carries the name as its abbreviation or short title, a trailing year aside, the tool uses that law. The answer names the law, why it was chosen and every other hit. Any other outcome returns the hits with their Gesetzesnummer, and the next call names one through `pick`. A `pick` counts only when it is among the hits for the name, because RIS reuses numbers across laws.

`date` reads a law as in force on that day, today if empty. A repealed law is found only with a date from its time in force.

`get_paragraph` takes RIS's own parameters rather than citation text, and the calling model converts the citation:

- `number`, the unit number with its suffix, sent to RIS as `Abschnitt.Von`
- `to`, the optional end of a range, at most 20 numbers further
- `kind`, one of `Paragraph`, `Artikel` or `Anlage`, or empty for every kind

Examples:

| Citation | number | to | kind |
|---|---|---|---|
| § 1295 ABGB | `1295` | | `Paragraph` |
| § 879a | `879a` | | `Paragraph` |
| Art. 7 B-VG, Artikel VII | `7` | | `Artikel` |
| Anlage 1, Anhang 1 | `1` | | `Anlage` |
| Art. 10 § 1 | `1` | | `Paragraph` |
| § 1 Abs. 2 Z 3 | `1` | | `Paragraph` |
| §§ 200–210 UGB | `200` | `210` | `Paragraph` |

RIS compares numbers only, so `5` also returns § 5a to § 5d, and § 1 of a law also returns the § 1 inside its articles. Every document RIS returns comes back under its RIS label, full text for the first 20 and the rest listed. Section headings such as Abschnitt or Hauptstück are not units; `get_law_outline` shows them.

## Hosted

```bash
claude mcp add --transport http ris https://ris-mcp.noahpfister.com/mcp
```

Claude Desktop accepts the same URL as a custom connector.

## Self-host

```bash
pip install -r requirements.txt
```

Point your MCP client at the local process:

```json
{
  "mcpServers": {
    "ris": {
      "command": "python3",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/ris-mcp"
    }
  }
}
```

`mcp dev src/server.py` runs it under the MCP Inspector.

`who_mentions` searches a local full-text index of about 600MB. It registers automatically once `data/ris.db` exists:

```bash
python3 -m src.index             # full crawl, ~441k docs at ~9/s, resumable
python3 -m src.index 10          # first 10 pages only
python3 -m src.index --fill-gaps # fetch missing docs only
```

## Configuration

All variables are optional. Defaults suit a local stdio run.

| Variable | Default | Purpose |
|---|---|---|
| `RIS_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `RIS_HOST` / `RIS_PORT` | `127.0.0.1` / `8000` | HTTP bind |
| `RIS_PUBLIC_HOSTS` | — | Comma-separated Host allowlist, required on non-loopback bind |
| `RIS_INDEX` | `auto` | `auto` registers `who_mentions` when the index exists, `on` requires it, `off` disables it |
| `RIS_DB_PATH` | `data/ris.db` | FTS index location |
| `RIS_STATIC_DIR` | — | Built landing page served at `/` |
| `RIS_MAX_UPSTREAM` | `4` | Concurrent requests to RIS per process |
| `RIS_RATE_LIMIT` / `RIS_RATE_WINDOW` | `60` / `60` | Per-IP token bucket, `0` disables it |
| `RIS_LOG_LEVEL` | `INFO` | |

## Tests

```bash
pytest           # offline: input parsing, refusals, HTTP failure handling
pytest -m live   # against the real RIS API
```

Everything that depends on RIS behaviour runs only in the live suite.

## Notes

- API base is `https://data.bka.gv.at/ris/api/v2.6/`, public and unauthenticated. `BrKons` serves consolidated federal law, `BgblAuth` serves gazette entries from 2004 on.
- `RIS_MAX_UPSTREAM` caps in-flight requests to RIS regardless of inbound load, which keeps a public endpoint from flooding a government API.

## Legal

Code is MIT licensed. Legal data comes from RIS, published by the Bundeskanzleramt under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en). Every tool response that passes on RIS data carries that attribution, keep it attached when quoting or re-serving output.

Only the Bundesgesetzblatt ("BGBl authentisch") is legally binding, consolidated texts returned here are not. RIS gives no warranty on accuracy or completeness, and nothing here is legal advice.

This is an independent project, not affiliated with the Republic of Austria, the Bundeskanzleramt or RIS. Forks should keep their name clear of anything suggesting an official service.

Running your own crawl means bulk access to a government API. RIS asks for 1–2s between paged requests, off-hours runs, a `User-Agent` header, and an email to ris.it@bka.gv.at with your IP and schedule beforehand.
