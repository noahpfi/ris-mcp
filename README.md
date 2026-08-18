# ris-mcp

MCP server for Austria's [RIS](https://www.ris.bka.gv.at/) (Rechtsinformationssystem).
Exposes Austrian federal law (Bundesrecht) to LLMs via the public RIS OGD API v2.6.

## Tools

Seven tools query the RIS API live and need nothing local. `who_mentions` is
backed by a full-text index you build yourself, so it is only registered where
that index exists.

| Tool | What it does | Hosted |
|---|---|---|
| `search_law` | Full-text search across Bundesrecht, newest first, deduplicated by law+paragraph | ✓ |
| `get_paragraph` | Fetch a paragraph or range (e.g. §§ 200–210 UGB), live version only | ✓ |
| `get_paragraph_at` | Historical version of a paragraph on a given date | ✓ |
| `get_statute` | Preamble + first page of live paragraphs for a statute | ✓ |
| `get_law_outline` | Full table of contents for a statute, grouped by section | ✓ |
| `lookup_bgbl` | Look up a BGBl entry by number (e.g. `50/2023`) | ✓ |
| `get_amendment_timeline` | Ordered list of every BGBl that amended a statute | ✓ |
| `who_mentions` | Reverse citation lookup: which provisions cite `§ 879`? | self-host |

## Use the hosted server

Nothing to install:

```bash
claude mcp add --transport http ris https://ris-mcp.noahpfister.com/mcp
```

Claude Desktop takes the same URL as a custom connector. The landing page is
served from the same origin — one hostname, one deployment.

## Self-host with cross-references

Adds `who_mentions`. Costs one long crawl and ~600MB of disk.

```bash
pip install -r requirements.txt
python3 -m src.index             # ~441k docs at ~9/s — an overnight run, resumable
python3 -m src.index 10          # first 10 pages only, to try it out
python3 -m src.index --fill-gaps # fetch only what the index is missing
```

Then point the client at the local process — `RIS_INDEX` defaults to `auto`, so
the tool appears once `data/ris.db` exists:

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

`mcp dev src/server.py` runs the same thing under the MCP Inspector.

## Run the hosted deployment yourself

Docker + Cloudflare Tunnel. Stateless: no volume, no crawler, no `who_mentions`.

One container serves both surfaces — the landing page at `/` and the MCP
endpoint at `/mcp`. The image builds the site itself in a Node stage, so there
is no separate static host to keep in sync and no way to ship a stale bundle.

Copy `.env.example` to `.env`, set `RIS_PUBLIC_HOSTS`, and point `CF_NETWORK` at
the docker network your cloudflared already runs on:

```bash
docker inspect <cloudflared-container> \
  --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
docker compose up -d --build
```

Then add **one** public hostname in the Cloudflare dashboard routing your domain
to `http://ris-mcp:8000`. No path rules: the container routes `/mcp` itself and
serves the page for everything else.

No tunnel yet? Add a connector to the same stack with a `compose.override.yaml`:

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${TUNNEL_TOKEN:?}
    depends_on: {ris-mcp: {condition: service_healthy}}
```

The container port is deliberately not published — the tunnel is the only route
in, which is what makes the `CF-Connecting-IP` header the rate limiter reads
trustworthy. On a shared network that trust extends to every container on it.

The rate limiter applies to `/mcp` only. Page views never reach the RIS API, so
throttling them would only break the surface meant to attract users.

To serve `who_mentions` from a container too, mount an index and flip the mode —
the directory must be writable by uid 10001 for the WAL sidecars:

```bash
docker run -v ./data:/data -e RIS_DB_PATH=/data/ris.db -e RIS_INDEX=on ris-mcp:latest
```

## Configuration

All optional; defaults suit a local stdio run.

| Variable | Default | Purpose |
|---|---|---|
| `RIS_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `RIS_HOST` / `RIS_PORT` | `127.0.0.1` / `8000` | HTTP bind |
| `RIS_PUBLIC_HOSTS` | — | Comma-separated Host allowlist. Required when binding a non-loopback address |
| `RIS_INDEX` | `auto` | `auto` registers `who_mentions` if the index file exists; `on` requires it, `off` never registers it |
| `RIS_DB_PATH` | `data/ris.db` | FTS index location |
| `RIS_STATIC_DIR` | — | Built landing page to serve at `/`. Set to `/app/website` in the image; unset locally |
| `RIS_MAX_UPSTREAM` | `4` | Concurrent requests in flight to RIS, per process |
| `RIS_RATE_LIMIT` / `RIS_RATE_WINDOW` | `60` / `60` | Per-IP inbound token bucket; `0` disables |
| `RIS_LOG_LEVEL` | `INFO` | |

## Tests

```bash
pytest
```

No network and no access to the real index — tests run against a temporary
database.

## Notes

- API: `https://data.bka.gv.at/ris/api/v2.6/` — public, no auth
- `Applikation=BrKons` for consolidated federal law, `BgblAuth` for gazette entries (≥2004)
- Content is HTML fetched per-document; converted to clean markdown
- The hosted server is intentionally unauthenticated. `RIS_MAX_UPSTREAM` caps
  requests in flight to RIS regardless of inbound load — that ceiling, not the
  per-IP limit, is what keeps a public endpoint from turning into an unbounded
  request rate against a government API under one address.
- Access logging is off: query strings and client addresses are personal data
  with no reason to be retained here.
- Crawling and indexing interleave. Collecting every missing reference before
  fetching held ~20KB each, which is ~5.7GB against a full gap — enough to get
  the crawler OOM-killed.

## Data, licence, disclaimer

The code is MIT (see `LICENSE`). The legal data is not mine: it comes from RIS,
published by the Bundeskanzleramt under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en), which requires
attribution wherever it is passed on — every tool response carries that line, so
leave it attached when you quote or re-serve the output.

RIS states there is no guarantee of accuracy, currency, completeness, or
availability, and that only the wording published in the Bundesgesetzblatt
("BGBl authentisch") or the respective Landesgesetzblatt is legally binding. The
consolidated texts these tools return are not. Nothing this server returns is
legal advice.

Independent project, not affiliated with, endorsed by, or operated by the
Republic of Austria, the Bundeskanzleramt, or RIS. If you fork it, keep the name
clear of any suggestion that it is an official federal service — RIS asks for
that explicitly.

Running your own crawl means bulk access to a government API: RIS asks for
1–2s between paged requests, out-of-hours runs, a `User-Agent` header, and an
email to ris.it@bka.gv.at with your IP and schedule before a mass download.
`src/index.py` paces itself; the notification is on you.
