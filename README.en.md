# docs-search

Zero-dependency local document search engine. Pure Python stdlib, SQLite index, millisecond-level retrieval.

[中文文档](README.md)

## Features

- **Zero dependencies** — Python stdlib only (3.10+), no pip install required to run
- **Fast** — SQLite index, retrieval < 50ms
- **Auto re-index** — detects file changes before every search
- **Web UI** — built-in search page with drag-and-drop `.md` upload
- **Multi-corpus isolation** — each docs directory gets its own index; coexist freely
- **Path-free** — no hardcoded paths; target dir via flag / env var / default
- **Cross-platform** — Windows / macOS / Linux

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

Uploads land in `<docs dir>/uploads/` and are indexed immediately; duplicate names get `-1`, `-2` suffixes.

## Security Notes

- Server binds `127.0.0.1` by default — **do not expose via `--host 0.0.0.0`** (no auth)
- Uploads: `.md` only, ≤ 10MB per file, sanitized filenames (path-traversal safe)
- Delete endpoint only touches files inside `uploads/`

## License

MIT — see [LICENSE](LICENSE)
