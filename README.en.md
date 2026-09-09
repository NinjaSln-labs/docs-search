# docs-search

Zero-dependency local document search engine. Pure Python stdlib, SQLite index, millisecond-level retrieval.

[中文文档](README.md)

## Features

- **Zero dependencies** — Python stdlib only (3.10+), no pip install required to run
- **Fast** — SQLite index, retrieval < 50ms
- **Auto re-index** — detects file changes before every search
- **AI Agent integration** — built-in MCP stdio server (Cursor / ZCode / Qoder / DSH / Cline / OpenCode / Reasonix / Command Code, plug-and-play) + pi extension; local mode or remote mode (`--url` to an already-running service, with optional Bearer/Basic auth); `docs-search-install` writes the config into every installed agent in one shot
- **Remote deployment** — `docs-search-web --host 0.0.0.0 --token <T>` as an independent remote service (non-loopback listening requires auth); backend and agents can be deployed separately
- **Web UI** — built-in search page with drag-and-drop `.md` upload
- **Multi-corpus isolation** — each docs directory gets its own index; every operation may carry a `workspace` to switch to a self-contained library (MCP/API/CLI alike); search/list support `workspace=all` cross-library aggregation
- **Safe uploads** — same-name conflicts default to a hint; force-overwrite or `-N` new file available; `.md` only, ≤10MB, sanitized
- **Path-free** — no hardcoded paths; target dir via flag / env var / default
- **Cross-platform** — Windows / macOS / Linux

## 📖 Documentation

**https://ninjasln-labs.github.io/docs-search/** — [Human Guide](https://ninjasln-labs.github.io/docs-search/#doc=HUMAN-GUIDE.md) · [Agent Integration Guide](https://ninjasln-labs.github.io/docs-search/#doc=MCP.md) · [Agent Guide](https://ninjasln-labs.github.io/docs-search/#doc=AGENT-GUIDE.md) · [AGENT-INDEX.json](https://ninjasln-labs.github.io/docs-search/AGENT-INDEX.json) · [API Reference](https://ninjasln-labs.github.io/docs-search/#doc=API.md) · [Server Contract](https://ninjasln-labs.github.io/docs-search/#doc=SERVER-CONTRACT.md)

## Quick Start

```bash
# 1. Index a docs directory (defaults to ./docs, or pass --dir)
python scripts/docs-search.py index --dir /path/to/your/docs

# 2. Search
python scripts/docs-search.py search "keyword" --dir /path/to/your/docs

# 3. Launch Web UI (includes upload endpoint)
python scripts/docs-search-web.py /path/to/your/docs
# open http://127.0.0.1:8765
```

Or install from source: `pip install .` → provides `docs-search` and `docs-search-web` commands.

## Path Resolution

| Target | Priority |
|--------|----------|
| Docs dir | `--dir` flag > `DOCS_SEARCH_DIR` env > `./docs` |
| Index DB | `--db` flag (CLI) > `DOCS_SEARCH_DB` env > `~/.docs-search/<dir-hash>/index.db` |

The index DB is namespaced by directory hash — multiple corpora coexist without interference.

## CLI

```bash
python scripts/docs-search.py index    [--dir DIR]      # rebuild index
python scripts/docs-search.py search "kw" [--dir DIR] [-n 8]  # multi-keyword AND search
python scripts/docs-search.py list     [--dir DIR]      # list all docs
python scripts/docs-search.py show <path> [--dir DIR]   # show content
python scripts/docs-search.py status   [--dir DIR]      # index status
python scripts/docs-search.py upload <file.md> [--dir DIR]  # copy .md into uploads/ and reindex
python scripts/docs-search.py open <path> [--dir DIR]   # open with system default app
```

## Web API

Launch: `python scripts/docs-search-web.py [DIR] [--port 8765] [--host 127.0.0.1] [--no-browser]`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/stats` | stats `{count, updated, categories}` |
| GET  | `/api/search?q=kw&cat=` | search (multi-keyword AND) |
| GET  | `/api/list?cat=` | list docs |
| GET  | `/api/show?path=x.md` | doc content |
| POST | `/api/upload?filename=x.md` | upload (raw body = UTF-8 text) |
| POST | `/api/delete?path=uploads/x.md` | delete an uploaded doc |

```bash
curl -X POST "http://127.0.0.1:8765/api/upload?filename=notes.md" --data-binary @notes.md
```

Uploads land in `<docs dir>/uploads/` and are indexed immediately; same-name conflicts default to a hint (409, nothing written) — use `if_exists=overwrite` to replace or `if_exists=keep` for `-1`/`-2` new files; every operation may carry a `workspace` for a self-contained library (omitted = default library).

## AI Agent Integration (MCP)

A zero-dependency MCP stdio server is built in, so AI agents can search/write the library directly:

```bash
# Cursor (~/.cursor/mcp.json)
{ "mcpServers": { "docs-search": { "command": "docs-search-mcp", "args": ["--dir", "/path/to/docs"] } } }

# Qoder CLI (-s user for global; CN edition uses `qodercn` — separate account from intl)
qodercn mcp add -s user docs-search -- docs-search-mcp --dir /path/to/docs
```

Exposes 5 tools: `docs_search` / `docs_read` / `docs_write` / `docs_delete` / `docs_info`.
One-shot setup for every installed agent (pi / cursor / cline / opencode / commandcode / zcode /
reasonix / qoder / dsh), idempotent with dry-run preview:

```bash
docs-search-install --dir D:/path/to/your/docs               # local mode
docs-search-install --url https://docs.example.com          # remote mode (auth via env)
docs-search-install --dir ./docs --agents pi,cursor         # subset
docs-search-install --dir ./docs --mcp-arg proxy=direct     # extra mcp args (repeatable)
docs-search-install --dir ./docs --dry-run                  # preview, no writes
```
For ZCode (Settings → MCP Servers), DSH (`@deepseek-ai/dsh-mcp-client` plugin row), Cline
(`~/.cline/data/settings/cline_mcp_settings.json`), OpenCode (`mcp.servers`), Reasonix
(`reasonix mcp add`), Command Code (`commandcode mcp add`) and the pi extension, see the
[Agent Integration Guide](docs/MCP.md).

When a docs-search service is already running (local or remote machine, same `/api/*` interface),
the MCP server can proxy to it instead of reading a local directory:

```bash
# Cursor (remote mode — service already up / shared library; add --token/--user/--password if the remote requires auth)
{ "mcpServers": { "docs-search": { "command": "docs-search-mcp", "args": ["--url", "http://192.168.1.10:8765", "--token", "<TOKEN>"] } } }
# or via env: DOCS_SEARCH_URL=http://192.168.1.10:8765 docs-search-mcp
# Connection proxy: follows env proxies by default; use --proxy direct to bypass, or --proxy http://proxy:port (DOCS_SEARCH_PROXY)

# Backend as a standalone remote service (auth required for non-loopback listening)
docs-search-web /path/to/docs --host 0.0.0.0 --token <TOKEN>
```

## Security Notes

- Server binds `127.0.0.1` by default — **non-loopback listening (e.g. `--host 0.0.0.0`) requires auth** (`--token` Bearer or `--user/--password` Basic), otherwise startup is refused
- Uploads: `.md` only, ≤ 10MB per file, sanitized filenames (path-traversal safe)
- Delete endpoint only touches files inside `uploads/`

## License

MIT — see [LICENSE](LICENSE)
