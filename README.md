# ris-mcp

MCP server for Austrian federal law (Bundesrecht), backed by the public [RIS](https://www.ris.bka.gv.at/) OGD API v2.6.

## Tools

| Tool | Purpose | Hosted |
|---|---|---|
| `search_law` | Full-text search across Bundesrecht, newest first, deduplicated by law and paragraph | ✓ |
| `get_paragraph` | Fetch a paragraph or range such as §§ 200–210 UGB, live version only | ✓ |
| `get_paragraph_at` | Historical version of a paragraph on a given date | ✓ |
| `get_statute` | Preamble and first page of live paragraphs for a statute | ✓ |
| `get_law_outline` | Full table of contents for a statute, grouped by section | ✓ |
| `lookup_bgbl` | Look up a BGBl entry by number, e.g. `50/2023` | ✓ |
| `get_amendment_timeline` | Every BGBl that amended a statute, in order | ✓ |
| `who_mentions` | Reverse citation lookup, e.g. which provisions cite `§ 879` | self-host |

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
pytest
```

Tests run offline against a temporary database.

## Notes

- API base is `https://data.bka.gv.at/ris/api/v2.6/`, public and unauthenticated. `BrKons` serves consolidated federal law, `BgblAuth` serves gazette entries from 2004 on.
- `RIS_MAX_UPSTREAM` caps in-flight requests to RIS regardless of inbound load, which keeps a public endpoint from flooding a government API.

## Legal

Code is MIT licensed. Legal data comes from RIS, published by the Bundeskanzleramt under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en). Every tool response carries that attribution, keep it attached when quoting or re-serving output.

Only the Bundesgesetzblatt ("BGBl authentisch") is legally binding, consolidated texts returned here are not. RIS gives no warranty on accuracy or completeness, and nothing here is legal advice.

This is an independent project, not affiliated with the Republic of Austria, the Bundeskanzleramt or RIS. Forks should keep their name clear of anything suggesting an official service.

Running your own crawl means bulk access to a government API. RIS asks for 1–2s between paged requests, off-hours runs, a `User-Agent` header, and an email to ris.it@bka.gv.at with your IP and schedule beforehand.
