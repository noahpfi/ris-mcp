# ris-mcp

MCP server for Austria's [RIS](https://www.ris.bka.gv.at/) (Rechtsinformationssystem).
Exposes Austrian federal law (Bundesrecht) to LLMs via the public RIS OGD API v2.6.

## Tools

| Tool | What it does |
|---|---|
| `search_law` | Full-text search across Bundesrecht, optionally scoped to one statute |
| `get_paragraph` | Fetch a paragraph or range (e.g. §§ 200–210 UGB) |
| `get_paragraph_at` | Historical version of a paragraph on a given date |
| `get_statute` | Preamble + first page of paragraphs for a statute |
| `lookup_bgbl` | Look up a BGBl entry by number (e.g. `50/2023`) |
| `get_amendment_timeline` | Ordered list of every BGBl that amended a statute |
| `who_mentions` | Full-text search local index for laws citing a reference |

## Install

```bash
pip install -r requirements.txt
```

## Run (MCP Inspector)

```bash
mcp dev src/server.py
```

## Claude Desktop

Add to `claude_desktop_config.json`:

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

## Build local index (for `who_mentions`)

```bash
python3 -m src.index        # crawl all ~250k docs (30-90 min)
python3 -m src.index 10     # crawl first 10 pages only (for testing)
```

## Notes

- API: `https://data.bka.gv.at/ris/api/v2.6/` — public, no auth
- `Applikation=BrKons` for consolidated federal law, `BgblAuth` for gazette entries (≥2004)
- Content is HTML fetched per-document; converted to clean markdown
