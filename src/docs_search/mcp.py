"""docs-search-mcp: docs-search 的 MCP(Model Context Protocol)server——stdio transport,纯标准库实现

对接支持 MCP 的 AI agent(cursor / zcode / qorder / dsh-mcp-client 等):
  stdio server:  docs-search-mcp --dir <文档目录>
  cursor:        ~/.cursor/mcp.json → {"command": "docs-search-mcp", "args": ["--dir", "..."]}
  qoder:         qoder mcp add docs -- docs-search-mcp --dir ...
  zcode:         Settings → MCP Servers → New MCP Server(stdio,Full configuration 可直接贴 JSON)
  dsh:           cordis.patch.yml 插件行 name: '@deepseek-ai/dsh-mcp-client' → command: docs-search-mcp

两种模式(二选一):
  本地模式(默认): 读本地文档目录 + 本地 SQLite 索引,零网络。
    路径: --dir > $DOCS_SEARCH_DIR > ./docs;索引按目录哈希隔离,搜索前自动增量重建。
  远程模式:       --url http://ip:port(或 $DOCS_SEARCH_URL)连接已运行的 docs-search 服务
    (docs-search-web 或任意接口相同的服务),不读本地目录、不起本地索引,5 个工具走
    HTTP 代理到该服务的 /api/*。服务端负责安全防护(上传 .md only/消毒、删除仅限 uploads/)。
    --url > $DOCS_SEARCH_URL > 本地模式。

远程认证: 服务端启用了认证时,用 --token(Bearer)或 --user/--password(Basic)携带凭据,
  环境变量回退: $DOCS_SEARCH_TOKEN / $DOCS_SEARCH_USER / $DOCS_SEARCH_PASSWORD。
连接代理: 远程模式连接层默认跟随环境/系统代理(http_proxy/all_proxy/no_proxy,urllib 惯例);
  --proxy <http://...>(或 $DOCS_SEARCH_PROXY)显式指定代理,--proxy direct 强制直连(清空代理);
  socks 代理不支持(纯标准库限制),配置时显式报错。

协议实现(JSON-RPC 2.0 over stdio,按行分隔——MCP stdio 惯例):
  initialize / notifications/initialized / ping
  tools/list → 5 个工具(docs_search / docs_read / docs_write / docs_delete / docs_info)
  tools/call → 复用 core 逻辑或代理远程服务;业务错误包进 isError 内容,不炸会话;未知方法返回 -32601

Windows 编码: 读写一律走 stdin/stdout 的二进制 buffer 显式 UTF-8,不依赖控制台代码页(坑: GBK mojibake)。
安全: 本地模式仅操作 --dir 指定目录;写入只进 uploads/ 并消毒;远程模式仅向 --url 指定服务发请求,
     安全防护由服务端强制执行——只连可信服务。
"""

import argparse
import json
import os
import sys
from urllib.request import getproxies

from . import __version__
from .core import (
    IF_EXISTS_CHOICES,
    MAX_UPLOAD_BYTES,
    ensure_index,
    get_conn,
    list_lib,
    list_workspaces,
    load_meta,
    rebuild_index,
    resolve_db_path,
    resolve_docs_dir,
    resolve_service_url,
    resolve_upload_target,
    resolve_workspace,
    resolve_workspace_paths,
    sanitize_filename,
    search_lib,
    win_utf8,
)
from .remote import RemoteClient, RemoteError

PROTOCOL_VERSION = "2024-11-05"  # MCP protocol revision(与主流 client 兼容的基线)
SERVER_NAME = "docs-search"
SERVER_VERSION = __version__  # 随包版本单一事实源
SNIPPET_LEN = 150

WORKSPACE_FIELD = {
    "type": "string",
    "description": "Optional workspace (self-contained library namespace). Omit for the default library",
}


def _lib(workspace, docs_dir, db_path):
    """操作级 workspace: 指定时切到自包含库(忽略默认库路径),否则返回默认 (docs_dir, db_path)（workspace=all 由调用方先拦截）"""
    ws = resolve_workspace(workspace)
    if not ws:
        return docs_dir, db_path
    d, p = resolve_workspace_paths(ws)
    return d.resolve(), p.resolve()


def _all_libs(docs_dir, db_path):
    """workspace=all 聚合: [(workspace 名或 None, docs_dir, db_path), ...]，默认库在前"""
    libs = [(None, docs_dir, db_path)]
    for name in list_workspaces():
        d, p = resolve_workspace_paths(name)
        libs.append((name, d.resolve(), p.resolve()))
    return libs


def _not_all(args, action):
    """写/读类操作拒绝 workspace=all，返回错误文本或 None"""
    if args.get("workspace") == "all":
        return f"workspace=all 仅支持搜索/列表/统计，不支持{action}"
    return None

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
                "workspace": WORKSPACE_FIELD,
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
            "properties": {
                "path": {"type": "string", "description": "Document relative path"},
                "workspace": WORKSPACE_FIELD,
            },
            "required": ["path"],
        },
    },
    {
        "name": "docs_write",
        "description": (
            "Write markdown content into the library as a new document (goes to uploads/, "
            "filename sanitized, <=10MB, UTF-8). Indexed immediately — searchable at once. "
            "if_exists: 'error' (default — same-name conflict returns a hint, nothing written), "
            "'overwrite' (force replace the existing file), 'keep' (auto-suffix -1/-2 new file). "
            "Use the returned path afterwards. Typical agent pattern: persist session "
            "conclusions/notes for future retrieval. Do NOT write secrets or private data — "
            "the library is readable by all local processes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Target file name, must end with .md (basename only)"},
                "content": {"type": "string", "description": "Markdown text (UTF-8)"},
                "if_exists": {
                    "type": "string",
                    "enum": ["error", "overwrite", "keep"],
                    "description": "Same-name conflict policy (default error)",
                },
                "workspace": WORKSPACE_FIELD,
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
            "properties": {
                "path": {"type": "string", "description": "Document relative path (must be uploads/...)"},
                "workspace": WORKSPACE_FIELD,
            },
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
                "workspace": WORKSPACE_FIELD,
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
    if args.get("workspace") == "all":
        return _search_all(docs_dir, db_path, args)
    docs_dir, db_path = _lib(args.get("workspace"), docs_dir, db_path)
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
    msg = _not_all(args, "读取")
    if msg:
        return _err(msg)
    docs_dir, db_path = _lib(args.get("workspace"), docs_dir, db_path)
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
    msg = _not_all(args, "写入")
    if msg:
        return _err(msg)
    docs_dir, db_path = _lib(args.get("workspace"), docs_dir, db_path)
    filename = sanitize_filename(str(args.get("filename") or ""))
    if not filename:
        return _err("文件名非法(仅支持 .md 且不含路径部分,如 session-2026-09-07.md)")
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return _err("content 必须是非空文本")
    if_exists = str(args.get("if_exists") or "error")
    if if_exists not in IF_EXISTS_CHOICES:
        return _err(f"未知 if_exists 取值: {if_exists!r}（可选: error/overwrite/keep）")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_UPLOAD_BYTES:
        return _err(f"内容过大(上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    target, msg = resolve_upload_target(docs_dir, filename, if_exists)
    if target is None:
        return _err(msg)
    target.write_bytes(encoded)
    n, _ = rebuild_index(docs_dir, db_path)
    verb = "overwrote" if if_exists == "overwrite" else "written"
    return _ok(f"{verb}: uploads/{target.name}(库内共 {n} 篇,已索引;后续用该路径 docs_read / docs_search)")


def _delete(docs_dir, db_path, args: dict) -> dict:
    msg = _not_all(args, "删除")
    if msg:
        return _err(msg)
    docs_dir, db_path = _lib(args.get("workspace"), docs_dir, db_path)
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
    if args.get("workspace") == "all":
        return _info_all(docs_dir, db_path, args)
    docs_dir, db_path = _lib(args.get("workspace"), docs_dir, db_path)
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


def _search_all(docs_dir, db_path, args: dict) -> dict:
    """workspace=all: 跨默认库 + 全部 workspace 搜索，结果带 [workspace] 来源标注"""
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("请输入关键词(query 为空)")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(limit, 20))
    cat = str(args.get("cat") or "")
    results = []
    for name, d, p in _all_libs(docs_dir, db_path):
        for r in search_lib(d, p, query, cat or None, limit):
            results.append((name, r))
    if not results:
        return _ok(f'no results for "{query}"（已搜默认库 + {len(_all_libs(docs_dir, db_path)) - 1} 个 workspace）')
    lines = [f'"{query}" -> {len(results)} results (workspace=all)']
    for name, r in results:
        tag = "" if name is None else f" [{name}]"
        lines.append(f"- {r['path']}{tag} | {r['title']}\n  {r['snippet']}")
    return _ok("\n".join(lines))


def _info_all(docs_dir, db_path, args: dict) -> dict:
    """workspace=all: 跨库枚举/统计，结果带 [workspace] 来源标注"""
    mode = str(args.get("mode") or "list")
    cat = str(args.get("cat") or "")
    libs = _all_libs(docs_dir, db_path)
    if mode == "stats":
        count, cats = 0, set()
        for name, d, p in libs:
            n, _ = ensure_index(d, p)
            count += load_meta(p).get("count", n)
            c = get_conn(p)
            for row in c.execute("SELECT DISTINCT cat FROM docs"):
                cats.add(row[0])
            c.close()
        return _ok(json.dumps(
            {"count": count, "updated": "?", "categories": sorted(cats),
             "workspaces": [n for n, _, _ in libs[1:]], "workspace": "all"},
            ensure_ascii=False))
    docs = []
    for name, d, p in libs:
        for r in list_lib(d, p, cat or None):
            docs.append((name, r))
    if not docs:
        return _ok(f"empty (workspace=all: 默认库 + {len(libs) - 1} 个 workspace)")
    lines = [f"{len(docs)} docs (workspace=all):"]
    for name, r in docs:
        tag = "" if name is None else f" [{name}]"
        lines.append(f"- {r['path']}{tag} | {r['title']} ({r['size'] // 1024}KB)")
    return _ok("\n".join(lines))


# ============================================================
# 远程模式工具实现 — 代理到已运行的 docs-search 服务(HTTP /api/*),
# 输出格式与本地模式一致,保证各 agent 行为一致
# ============================================================

def _remote_search(client, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return _err("请输入关键词(query 为空)")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(limit, 20))
    cat = str(args.get("cat") or "")
    data = client.search(query, cat or None, workspace=args.get("workspace"))
    if "error" in data:
        return _err(str(data["error"]))
    results = (data.get("results") or [])[:limit]
    if not results:
        return _ok(f'no results for "{query}"（可减少关键词、换词,或 docs_info 核对）')
    lines = [f'"{query}" -> {len(results)} results']
    for r in results:
        lines.append(f"- {r.get('path')} | {r.get('title', '')}\n  {(r.get('snippet') or '').replace(chr(10), ' ')}")
    return _ok("\n".join(lines))


def _remote_read(client, args: dict) -> dict:
    p = str(args.get("path") or "").strip()
    if not p:
        return _err("缺少 path 参数")
    data = client.show(p, workspace=args.get("workspace"))
    if "error" in data:
        return _err(f"文档不存在: {p}(先 docs_info mode=list 获取准确相对路径)")
    title, body, size, cat_val = data.get("title", ""), data.get("body", ""), data.get("size", 0), data.get("cat", "")
    head = "" if body.startswith("# ") else f"# {title}\n({data.get('path', p)} | {cat_val} | {size // 1024}KB)\n\n"
    return _ok(f"{head}{body}")


def _remote_write(client, args: dict) -> dict:
    filename = sanitize_filename(str(args.get("filename") or ""))
    if not filename:
        return _err("文件名非法(仅支持 .md 且不含路径部分,如 session-2026-09-07.md)")
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return _err("content 必须是非空文本")
    if_exists = str(args.get("if_exists") or "error")
    if if_exists not in IF_EXISTS_CHOICES:
        return _err(f"未知 if_exists 取值: {if_exists!r}（可选: error/overwrite/keep）")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        return _err(f"内容过大(上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    data = client.upload(filename, content, if_exists, workspace=args.get("workspace"))
    if "error" in data:
        return _err(f"上传失败: {data['error']}")
    verb = "overwrote" if if_exists == "overwrite" else "written"
    return _ok(f"{verb}: {data.get('path')}(库内共 {data.get('count', '?')} 篇,已索引;后续用该路径 docs_read / docs_search)")


def _remote_delete(client, args: dict) -> dict:
    rel = str(args.get("path") or "").replace("\\", "/")
    if not rel:
        return _err("缺少 path 参数")
    data = client.delete(rel, workspace=args.get("workspace"))
    if "error" in data:
        return _err(str(data["error"]))
    return _ok(f"deleted: {rel}(库内共 {data.get('count', '?')} 篇)")


def _remote_info(client, args: dict) -> dict:
    mode = str(args.get("mode") or "list")
    cat = str(args.get("cat") or "")
    if mode == "stats":
        data = client.stats(workspace=args.get("workspace"))
        if "error" in data:
            return _err(str(data["error"]))
        return _ok(json.dumps(
            {"count": data.get("count", "?"), "updated": data.get("updated", "?"), "categories": data.get("categories", [])},
            ensure_ascii=False))
    data = client.list(cat or None, workspace=args.get("workspace"))
    if "error" in data:
        return _err(str(data["error"]))
    docs = data.get("docs") or []
    if not docs:
        return _ok(f"empty (remote={client.base})")
    lines = [f"{len(docs)} docs:"]
    for d in docs:
        lines.append(f"- {d.get('path')} | {d.get('title', '')} ({d.get('size', 0) // 1024}KB)")
    return _ok("\n".join(lines))


REMOTE_HANDLERS = {
    "docs_search": _remote_search,
    "docs_read": _remote_read,
    "docs_write": _remote_write,
    "docs_delete": _remote_delete,
    "docs_info": _remote_info,
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


def _dispatch(req: dict, docs_dir, db_path, client=None) -> None:
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
        handler = (REMOTE_HANDLERS if client is not None else HANDLERS).get(name)
        if not handler:
            _reply_error(req_id, -32602, f"Unknown tool: {name}")
            return
        args = params.get("arguments") or {}
        try:
            if client is not None:
                result = handler(client, args)
            else:
                result = handler(docs_dir, db_path, args)
        except RemoteError as e:
            result = _err(f"远程服务错误: {e}")
        except Exception as e:  # noqa: BLE001 -- 工具错误进内容,不炸会话
            result = _err(f"docs-search 工具执行失败: {e}")
        _reply(req_id, result)
    elif req_id is not None:
        # 未知请求(resources/list、prompts/list 等)→ 规范 Method not found
        _reply_error(req_id, -32601, f"Method not found: {method}")
    # 无 id 的通知(initialized 等)→ 静默忽略


def serve(stdin_buf, docs_dir=None, db_path=None, client=None) -> None:
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
        _dispatch(req, docs_dir, db_path, client)


def main():
    win_utf8()
    parser = argparse.ArgumentParser(
        prog="docs-search-mcp",
        description="docs-search MCP server (stdio) — 供 cursor/zcode/qorder/dsh 等 MCP 客户端对接;"
        "默认本地模式,可用 --url 切远程模式(连接已运行的 docs-search 服务)",
    )
    parser.add_argument("dir_pos", nargs="?", default=None, help="文档根目录(默认 ./docs 或 $DOCS_SEARCH_DIR;远程模式忽略)")
    parser.add_argument("--dir", dest="dir", default=None, help="同位置参数,二选一")
    parser.add_argument("--db", default=None, help="索引库路径(默认 ~/.docs-search/<目录哈希>/index.db)")
    parser.add_argument(
        "--url", default=None,
        help="连接已运行的 docs-search 服务(如 http://192.168.1.10:8765);提供后走远程模式,不读本地目录。"
        "默认 $DOCS_SEARCH_URL;未配置则为本地模式",
    )
    parser.add_argument("--token", default=None, help="远程模式 Bearer token(默认 $DOCS_SEARCH_TOKEN)")
    parser.add_argument("--user", default=None, help="远程模式 Basic 认证用户名(默认 $DOCS_SEARCH_USER;与 --password 成对)")
    parser.add_argument("--password", default=None, help="远程模式 Basic 认证密码(默认 $DOCS_SEARCH_PASSWORD)")
    parser.add_argument(
        "--proxy", default=None,
        help="远程模式连接代理(如 http://proxy.corp:8080;裸 host:port 自动补 scheme);"
        "direct/none 强制直连(忽略 http_proxy/all_proxy 等环境代理)。"
        "默认 $DOCS_SEARCH_PROXY;未配置则跟随环境/系统代理",
    )
    args = parser.parse_args()

    url = resolve_service_url(args.url)
    if url:
        token = args.token or os.environ.get("DOCS_SEARCH_TOKEN")
        user = args.user or os.environ.get("DOCS_SEARCH_USER")
        password = args.password or os.environ.get("DOCS_SEARCH_PASSWORD")
        proxy = args.proxy or os.environ.get("DOCS_SEARCH_PROXY") or None
        if token and (user or password):
            print("错误: --token 与 --user/--password 互斥，只能启用一种认证方式", file=sys.stderr)
            sys.exit(1)
        if bool(user) != bool(password):
            print("错误: --user 与 --password 必须成对提供", file=sys.stderr)
            sys.exit(1)
        try:
            client = RemoteClient(url, token=token, username=user, password=password, proxy=proxy)
        except RemoteError as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
        # 启动探活: 服务不可达/未授权立刻报错退出(MCP 客户端会展示并自动重启重试),而非挂起无输出
        try:
            probe = client.stats()
        except RemoteError as e:
            print(f"错误: 无法连接 docs-search 服务({url}): {e}", file=sys.stderr)
            print("提示: 先启动 docs-search-web,或确认 --url/$DOCS_SEARCH_URL 指向带 /api/* 的服务", file=sys.stderr)
            if proxy is None and getproxies():
                print("提示: 检测到环境/系统代理(http_proxy 等)——若服务在本机/内网,可 --proxy direct 直连", file=sys.stderr)
            sys.exit(1)
        if "error" in probe:
            print(f"错误: 服务探活失败: {probe['error']}", file=sys.stderr)
            sys.exit(1)
        serve(sys.stdin.buffer, client=client)
        return

    docs_dir = resolve_docs_dir(args.dir or args.dir_pos)
    db_path = resolve_db_path(docs_dir, args.db)

    if not docs_dir.exists():
        # 警告走 stderr(stdio 保持协议纯净);目录照常创建,空库可通过 docs_info 观察
        print(f"警告: 文档目录不存在,已创建空库: {docs_dir}", file=sys.stderr)
        docs_dir.mkdir(parents=True, exist_ok=True)

    serve(sys.stdin.buffer, docs_dir, db_path)


if __name__ == "__main__":
    main()
