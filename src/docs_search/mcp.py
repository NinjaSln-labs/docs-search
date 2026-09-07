"""docs-search-mcp: docs-search 的 MCP(Model Context Protocol)server——stdio transport,纯标准库实现

对接支持 MCP 的 AI agent(cursor / zcode / qorder / dsh-mcp-client 等):
  stdio server:  docs-search-mcp --dir <文档目录>
  cursor:        ~/.cursor/mcp.json → {"command": "docs-search-mcp", "args": ["--dir", "..."]}
  qoder:         qoder mcp add docs -- docs-search-mcp --dir ...
  zcode:         Settings → MCP Servers → New MCP Server(stdio,Full configuration 可直接贴 JSON)
  dsh:           cordis.patch.yml 插件行 name: '@deepseek-ai/dsh-mcp-client' → command: docs-search-mcp

协议实现(JSON-RPC 2.0 over stdio,按行分隔——MCP stdio 惯例):
  initialize / notifications/initialized / ping
  tools/list → 5 个工具(docs_search / docs_read / docs_write / docs_delete / docs_info)
  tools/call → 复用 core 逻辑;业务错误包进 isError 内容,不炸会话;未知方法返回 -32601

路径规则与 core 一致: --dir > $DOCS_SEARCH_DIR > ./docs;索引按目录哈希隔离,搜索前自动增量重建。
Windows 编码: 读写一律走 stdin/stdout 的二进制 buffer 显式 UTF-8,不依赖控制台代码页(坑: GBK mojibake)。
安全: 仅操作 --dir 指定目录;写入只进 uploads/ 并消毒;无网络(纯 stdio,无出站请求)。
"""

import argparse
import json
import sys

from .core import (
    MAX_UPLOAD_BYTES,
    dedupe_target,
    ensure_index,
    get_conn,
    load_meta,
    rebuild_index,
    resolve_db_path,
    resolve_docs_dir,
    sanitize_filename,
    win_utf8,
)

PROTOCOL_VERSION = "2024-11-05"  # MCP protocol revision(与主流 client 兼容的基线)
SERVER_NAME = "docs-search"
SERVER_VERSION = "1.0.0"
SNIPPET_LEN = 150

# ============================================================
# 工具定义(JSON Schema)— 单一事实源,tools/list 与文档共用
# ============================================================

TOOLS = [
    {
        "name": "docs_search",
        "description": (
            "Search a local markdown document library (agent memory / knowledge base). "
            "Keywords are space-separated and ANDed (exact substring match, CJK included). "
            "Returns matching paths with 150-char snippets; follow up with docs_read for "
            "full text. The index auto-refreshes on every call — no manual maintenance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, space-separated (AND semantics)"},
                "limit": {"type": "integer", "description": "Max results (default 8, cap 20)"},
                "cat": {"type": "string", "description": "Optional category filter (top-level folder name)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "docs_read",
        "description": (
            "Read a document's full text from the library by relative path (e.g. "
            "'infra/mcp.md' or 'uploads/notes.md'). Use docs_info mode=list first if the "
            "path is unknown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Document relative path"}},
            "required": ["path"],
        },
    },
    {
        "name": "docs_write",
        "description": (
            "Write markdown content into the library as a new document (goes to uploads/, "
            "filename sanitized, <=10MB, UTF-8). Indexed immediately — searchable at once. "
            "Duplicate names get -1/-2 suffixes; use the returned path afterwards. Typical "
            "agent pattern: persist session conclusions/notes for future retrieval. Do NOT "
            "write secrets or private data — the library is readable by all local processes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target file name, must end with .md (basename only)"},
                "content": {"type": "string", "description": "Markdown text (UTF-8)"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "docs_delete",
        "description": (
            "Delete a previously written document. Only files under uploads/ can be deleted "
            "(library files are managed by their owner — attempts return an error by design)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Document relative path (must be uploads/...)"}},
            "required": ["path"],
        },
    },
    {
        "name": "docs_info",
        "description": (
            "Enumerate the library. mode='list' returns all documents (path/title/size, "
            "optionally filtered by cat); mode='stats' returns document count, last index "
            "time and the category list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["list", "stats"], "description": "list (default) or stats"},
                "cat": {"type": "string", "description": "Optional category filter (list mode only)"},
            },
        },
    },
]


# ============================================================
# 工具实现 — 复用 core;业务错误以 isError 内容返回,不抛协议错误
# ============================================================

def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _search(docs_dir, db_path, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("请输入关键词(query 为空)")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(limit, 20))
    cat = str(args.get("cat") or "")
    ensure_index(docs_dir, db_path)
    conditions, pargs = [], []
    for kw in query.split():
        conditions.append("(title LIKE ? OR body LIKE ?)")
        pargs.extend([f"%{kw}%", f"%{kw}%"])
    sql = f"SELECT path, cat, title, body FROM docs WHERE {' AND '.join(conditions)}"
    if cat:
        sql += " AND cat = ?"
        pargs.append(cat)
    sql += " LIMIT ?"
    pargs.append(limit)
    c = get_conn(db_path)
    rows = c.execute(sql, pargs).fetchall()
    c.close()
    if not rows:
        return _ok(
            f'no results for "{query}"'
            f"(库内 {load_meta(db_path).get('count', '?')} 篇;可减少关键词、换词,或 docs_info 核对)"
        )
    lines = [f'"{query}" -> {len(rows)} results']
    for path, cat_val, title, body in rows:
        lines.append(f"- {path} | {title}\n  {body[:SNIPPET_LEN].replace(chr(10), ' ')}")
    return _ok("\n".join(lines))


def _read(docs_dir, db_path, args: dict) -> dict:
    p = str(args.get("path") or "").strip()
    if not p:
        return _err("缺少 path 参数")
    ensure_index(docs_dir, db_path)
    c = get_conn(db_path)
    row = c.execute("SELECT title, body, size, cat FROM docs WHERE path=?", (p,)).fetchone()
    c.close()
    if not row:
        return _err(f"文档不存在: {p}(先 docs_info mode=list 获取准确相对路径)")
    title, body, size, cat_val = row
    head = "" if body.startswith("# ") else f"# {title}\n({p} | {cat_val} | {size // 1024}KB)\n\n"
    return _ok(f"{head}{body}")


def _write(docs_dir, db_path, args: dict) -> dict:
    filename = sanitize_filename(str(args.get("filename") or ""))
    if not filename:
        return _err("文件名非法(仅支持 .md 且不含路径部分,如 session-2026-09-07.md)")
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return _err("content 必须是非空文本")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_UPLOAD_BYTES:
        return _err(f"内容过大(上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    target = dedupe_target(docs_dir, filename)
    if not target:
        return _err("无法分配目标文件名")
    target.write_bytes(encoded)
    n, _ = rebuild_index(docs_dir, db_path)
    return _ok(f"written: uploads/{target.name}(库内共 {n} 篇,已索引;后续用该路径 docs_read / docs_search)")


def _delete(docs_dir, db_path, args: dict) -> dict:
    rel = str(args.get("path") or "").replace("\\", "/")
    uploads_root = (docs_dir / "uploads").resolve()
    target = (docs_dir / rel).resolve()
    # 仅允许删除 uploads/ 下的文件,且防路径穿越(与 HTTP API 同一语义)
    if uploads_root not in target.parents or target == uploads_root:
        return _err("仅允许删除 uploads/ 下的文档")
    if not target.exists():
        return _err(f"文档不存在: {rel}")
    try:
        target.unlink()
    except OSError as e:
        return _err(f"删除失败: {e}")
    n, _ = rebuild_index(docs_dir, db_path)
    return _ok(f"deleted: {rel}(库内共 {n} 篇)")


def _info(docs_dir, db_path, args: dict) -> dict:
    mode = str(args.get("mode") or "list")
    cat = str(args.get("cat") or "")
    ensure_index(docs_dir, db_path)
    if mode == "stats":
        meta = load_meta(db_path)
        c = get_conn(db_path)
        cats = sorted({r[0] for r in c.execute("SELECT DISTINCT cat FROM docs")})
        c.close()
        return _ok(json.dumps(
            {"count": meta.get("count", "?"), "updated": meta.get("updated", "?"), "categories": cats},
            ensure_ascii=False))
    c = get_conn(db_path)
    if cat:
        rows = c.execute("SELECT path, title, size FROM docs WHERE cat=? ORDER BY title", (cat,)).fetchall()
    else:
        rows = c.execute("SELECT path, title, size FROM docs ORDER BY cat, title").fetchall()
    c.close()
    if not rows:
        return _ok(f"empty (docs_dir={docs_dir};确认 --dir 是否指向预期目录)")
    lines = [f"{len(rows)} docs:"]
    for path, title, size in rows:
        lines.append(f"- {path} | {title} ({size // 1024}KB)")
    return _ok("\n".join(lines))


HANDLERS = {
    "docs_search": _search,
    "docs_read": _read,
    "docs_write": _write,
    "docs_delete": _delete,
    "docs_info": _info,
}


# ============================================================
# JSON-RPC 2.0 over stdio(按行分隔;显式 UTF-8 字节,不依赖控制台代码页)
# ============================================================

def _send(obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _reply(req_id, result):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _reply_error(req_id, code, message):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _dispatch(req: dict, docs_dir, db_path) -> None:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        _reply(req_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "ping":
        _reply(req_id, {})
    elif method == "tools/list":
        _reply(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name", "")
        handler = HANDLERS.get(name)
        if not handler:
            _reply_error(req_id, -32602, f"Unknown tool: {name}")
            return
        try:
            result = handler(docs_dir, db_path, params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001 -- 工具错误进内容,不炸会话
            result = _err(f"docs-search 工具执行失败: {e}")
        _reply(req_id, result)
    elif req_id is not None:
        # 未知请求(resources/list、prompts/list 等)→ 规范 Method not found
        _reply_error(req_id, -32601, f"Method not found: {method}")
    # 无 id 的通知(initialized 等)→ 静默忽略


def serve(stdin_buf, docs_dir, db_path) -> None:
    for raw in stdin_buf:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue  # 非 JSON 行(噪声/日志)忽略,保持 stdio 纯净
        if not isinstance(req, dict):
            continue
        _dispatch(req, docs_dir, db_path)


def main():
    win_utf8()
    parser = argparse.ArgumentParser(
        prog="docs-search-mcp",
        description="docs-search MCP server (stdio) — 供 cursor/zcode/qorder/dsh 等 MCP 客户端对接",
    )
    parser.add_argument("dir_pos", nargs="?", default=None, help="文档根目录(默认 ./docs 或 $DOCS_SEARCH_DIR)")
    parser.add_argument("--dir", dest="dir", default=None, help="同位置参数,二选一")
    parser.add_argument("--db", default=None, help="索引库路径(默认 ~/.docs-search/<目录哈希>/index.db)")
    args = parser.parse_args()

    docs_dir = resolve_docs_dir(args.dir or args.dir_pos)
    db_path = resolve_db_path(docs_dir, args.db)

    if not docs_dir.exists():
        # 警告走 stderr(stdio 保持协议纯净);目录照常创建,空库可通过 docs_info 观察
        print(f"警告: 文档目录不存在,已创建空库: {docs_dir}", file=sys.stderr)
        docs_dir.mkdir(parents=True, exist_ok=True)

    serve(sys.stdin.buffer, docs_dir, db_path)


if __name__ == "__main__":
    main()
