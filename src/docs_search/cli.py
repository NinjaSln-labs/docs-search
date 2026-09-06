"""docs-search: 本地文档搜索引擎（零依赖，SQLite，毫秒级检索）

用法:
  python scripts/docs-search.py index [--dir 目录]
  python scripts/docs-search.py search "关键词" [--dir 目录]
  python scripts/docs-search.py list [--dir 目录]
  python scripts/docs-search.py show <path> [--dir 目录]
  python scripts/docs-search.py status [--dir 目录]
  python scripts/docs-search.py upload <file.md> [--dir 目录]  # 上传（复制）文档

路径规则（不绑定任何本地路径）:
  文档目录: --dir > 环境变量 DOCS_SEARCH_DIR > ./docs
  索引库:   ~/.docs-search/<目录哈希>/index.db（按目录隔离，多库并存）

跨平台: Windows / macOS / Linux
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .core import (
    MAX_UPLOAD_BYTES,
    dedupe_target,
    ensure_index,
    get_conn,
    load_meta,
    resolve_db_path,
    resolve_docs_dir,
    sanitize_filename,
    win_utf8,
)

# 短命令别名
ALIAS = {"i": "index", "s": "search", "st": "status", "l": "list", "sh": "show", "u": "upload"}


def _paths(args):
    docs_dir = resolve_docs_dir(getattr(args, "dir", None))
    db_path = resolve_db_path(docs_dir, getattr(args, "db", None))
    return docs_dir, db_path


def cmd_index(args):
    win_utf8()
    docs_dir, db_path = _paths(args)
    from .core import rebuild_index

    n, dt = rebuild_index(docs_dir, db_path)
    print(f"indexed {n} docs in {dt:.0f}ms")
    print(f"  docs: {docs_dir}")
    print(f"  db:   {db_path}")


def cmd_search(args):
    win_utf8()
    docs_dir, db_path = _paths(args)
    import time

    t0 = time.time()
    n, rebuilt = ensure_index(docs_dir, db_path)
    if rebuilt:
        print(f"[auto] reindexed {n} docs")
    c = get_conn(db_path)
    q = args.query.strip()
    keywords = q.split()
    if not keywords:
        print('no results for ""')
        return
    conditions, params = [], []
    for kw in keywords:
        conditions.append("(title LIKE ? OR body LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%"])
    sql = f"SELECT path, cat, title, body FROM docs WHERE {' AND '.join(conditions)} LIMIT ?"
    params.append(args.limit)
    rows = c.execute(sql, params).fetchall()
    c.close()
    dt = (time.time() - t0) * 1000
    if not rows:
        print(f'no results for "{q}"')
        return
    print(f'"{q}" -> {len(rows)} results ({dt:.0f}ms)\n')
    for path, cat, title, body in rows:
        print(f"[{path}] {title}")
        print(f"  {body[:120].replace(chr(10), ' ')}...")
        print()


def cmd_list(args):
    win_utf8()
    _docs_dir, db_path = _paths(args)
    c = get_conn(db_path)
    rows = c.execute("SELECT cat, path, title, size FROM docs ORDER BY cat, title").fetchall()
    c.close()
    if not rows:
        print("empty")
        return
    print(f"total: {len(rows)} docs\n")
    cur = None
    for cat, path, title, size in rows:
        if cat != cur:
            cur = cat
            print(f"\n### {cat}/")
        print(f"  . {path} -- {title} ({size // 1024}KB)")


def cmd_show(args):
    win_utf8()
    _docs_dir, db_path = _paths(args)
    c = get_conn(db_path)
    row = c.execute("SELECT title, body, size FROM docs WHERE path=?", (args.path,)).fetchone()
    c.close()
    if not row:
        print(f"not found: {args.path}")
        return
    title, body, size = row
    print(f"\n{title} [{args.path}] ({size // 1024}KB)\n{body[:2000]}")
    if len(body) > 2000:
        print(f"\n...(total {len(body) // 1024}KB)")
    print()


def cmd_status(args):
    win_utf8()
    docs_dir, db_path = _paths(args)
    from .core import need_reindex

    meta = load_meta(db_path)
    if not db_path.exists():
        print(f"docs: {docs_dir}")
        print("no index yet. run: docs-search index")
        return
    c = get_conn(db_path)
    count = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    c.close()
    stale = need_reindex(meta, db_path, docs_dir)
    print(f"docs:    {docs_dir}")
    print(f"db:      {db_path}")
    print(f"records: {count}")
    if meta:
        print(f"updated: {meta.get('updated', '?')}")
        print(f"stale:   {'yes (search will auto-reindex)' if stale else 'no'}")


def cmd_upload(args):
    """把一个 .md 文件复制到文档库 uploads/ 并重建索引"""
    win_utf8()
    docs_dir, db_path = _paths(args)
    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"not found: {src}")
        sys.exit(1)
    if src.stat().st_size > MAX_UPLOAD_BYTES:
        print(f"too large (max {MAX_UPLOAD_BYTES // 1024 // 1024}MB): {src.name}")
        sys.exit(1)
    name = sanitize_filename(src.name)
    if not name:
        print(f"invalid filename: {src.name}")
        sys.exit(1)
    target = dedupe_target(docs_dir, name)
    if not target:
        print("upload failed: cannot allocate target name")
        sys.exit(1)
    target.write_bytes(src.read_bytes())
    from .core import rebuild_index

    n, dt = rebuild_index(docs_dir, db_path)
    print(f"uploaded: {target.name} -> uploads/{target.name}")
    print(f"reindexed {n} docs in {dt:.0f}ms")


def cmd_open(args):
    win_utf8()
    docs_dir, _ = _paths(args)
    p = docs_dir / args.path.replace("\\", "/")
    if not p.exists():
        print(f"not found: {args.path}")
        return
    if sys.platform == "win32":
        subprocess.Popen(["start", "/B", "", str(p)], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    p = argparse.ArgumentParser(prog="docs-search", description="docs-search: 本地文档搜索引擎（零依赖）")
    sub = p.add_subparsers(dest="cmd")

    def add(name, aliases, help_text):
        sp = sub.add_parser(name, aliases=aliases, help=help_text)
        sp.add_argument("--dir", default=None, help="文档根目录（默认 ./docs 或 $DOCS_SEARCH_DIR）")
        sp.add_argument("--db", default=None, help="索引库路径（默认 ~/.docs-search/<目录哈希>/index.db）")
        return sp

    add("index", ["i"], "重建索引")
    sp_search = add("search", ["s"], "搜索文档")
    sp_search.add_argument("query", help="搜索关键词（多关键词 AND）")
    sp_search.add_argument("--limit", "-n", type=int, default=8, help="结果条数（默认 8）")
    add("list", ["l"], "列出所有文档")
    sp_show = add("show", ["sh"], "显示文档内容")
    sp_show.add_argument("path", help="文档相对路径（如 infra/mcp.md）")
    add("status", ["st"], "查看索引状态")
    sp_up = add("upload", ["u"], "上传（复制）.md 文档到 uploads/ 并重建索引")
    sp_up.add_argument("file", help="要上传的 .md 文件路径")
    sp_open = add("open", ["o"], "用系统默认程序打开文档")
    sp_open.add_argument("path", help="文档相对路径")

    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return
    fn = {
        "index": cmd_index,
        "search": cmd_search,
        "list": cmd_list,
        "show": cmd_show,
        "status": cmd_status,
        "upload": cmd_upload,
        "open": cmd_open,
    }.get(ALIAS.get(a.cmd, a.cmd))
    if fn:
        fn(a)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
