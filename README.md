# ris-mcp

MCP server for Austria's [RIS](https://www.ris.bka.gv.at/) (Rechtsinformationssystem).
Exposes Austrian federal law (Bundesrecht) to LLMs via the public RIS OGD API v2.6.

## Tools

| Tool | What it does |
|---|---|
| `search_law` | Full-text search across Bundesrecht, newest first, deduplicated by law+paragraph |
| `get_paragraph` | Fetch a paragraph or range (e.g. §§ 200–210 UGB), live version only |
| `get_paragraph_at` | Historical version of a paragraph on a given date |
| `get_statute` | Preamble + first page of live paragraphs for a statute |
| `get_law_outline` | Full table of contents for a statute, grouped by section |
| `lookup_bgbl` | Look up a BGBl entry by number (e.g. `50/2023`) |
| `get_amendment_timeline` | Ordered list of every BGBl that amended a statute |
| `who_mentions` | Search local FTS index for laws citing a provision (e.g. `§ 879`) |

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
