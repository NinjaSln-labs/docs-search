"""docs_search.core: docs-search 共享核心模块（CLI 与 Web 共用，零第三方依赖）

路径解析规则（不绑定任何本地/个人路径）:
  文档目录:  --dir 参数 > 环境变量 DOCS_SEARCH_DIR > ./docs（当前工作目录下）
  索引库:    --db 参数 > 环境变量 DOCS_SEARCH_DB > ~/.docs-search/<目录哈希>/index.db
             按文档目录哈希隔离，多个文档库可并存互不干扰。
  工作空间:  每次操作可带 workspace（MCP 工具参数 / Web API ?ws= / CLI --workspace）；
             指定时使用自包含库 ~/.docs-search/workspaces/<ws>/（docs 目录 + index.db），
             不指定 = 默认库。操作级概念，不在服务启动时绑定。
  远程服务:  --url 参数 > 环境变量 DOCS_SEARCH_URL（可选；配置后走远程模式，
             不读本地目录，改连已运行的 docs-search 服务，见 remote.py）

上传同名策略（if_exists）: error（默认，重名提示）> overwrite（强制覆盖）> keep（生成 -N 新文件）
"""

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ENV_DOCS_DIR = "DOCS_SEARCH_DIR"
ENV_DB_PATH = "DOCS_SEARCH_DB"
ENV_SERVICE_URL = "DOCS_SEARCH_URL"
DEFAULT_DOCS_DIRNAME = "docs"

# 上传同名冲突策略（error=默认提示 / overwrite=覆盖 / keep=生成 -N 新文件）
IF_EXISTS_CHOICES = ("error", "overwrite", "keep")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 上传体积上限 10MB


def win_utf8():
    """Windows 控制台 GBK 兼容: 强制 UTF-8 输出"""
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        import io

        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001, S110 -- 控制台包装失败时静默降级
            pass


def resolve_docs_dir(explicit=None, workspace=None):
    """解析文档根目录: workspace 优先（自包含库）> --dir > $DOCS_SEARCH_DIR > ./docs"""
    ws = resolve_workspace(workspace)
    if ws:
        docs, _ = resolve_workspace_paths(ws)
        return docs.resolve()
    if explicit:
        p = Path(explicit).expanduser()
    elif os.environ.get(ENV_DOCS_DIR):
        p = Path(os.environ[ENV_DOCS_DIR]).expanduser()
    else:
        p = Path.cwd() / DEFAULT_DOCS_DIRNAME
    return p.resolve()


def resolve_service_url(explicit=None):
    """解析远程服务地址: --url > $DOCS_SEARCH_URL;未配置返回 None(本地模式)"""
    val = explicit or os.environ.get(ENV_SERVICE_URL) or ""
    return val.strip() or None


def resolve_workspace(explicit=None):
    """解析工作空间名（操作级）: 未配置返回 None（默认库）。
    危险字符消毒（用于目录名，防路径穿越）"""
    import re

    val = explicit or ""
    val = val.strip()
    if not val:
        return None
    val = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", val).strip(". ")
    return val or None


def resolve_workspace_paths(workspace):
    """workspace 自包含库的物理位置: (docs 目录, 索引库路径)。
    独立于 --dir/默认目录，天然分割；目录首次使用时由调用方创建。"""
    root = Path.home() / ".docs-search" / "workspaces" / resolve_workspace(workspace)
    return root / "docs", root / "index.db"


def resolve_paths(docs_dir_explicit=None, db_explicit=None, workspace=None):
    """解析 (文档目录, 索引库) 对——操作级 workspace 的单点入口。
    workspace 指定 → 自包含库（忽略 --dir）；否则 --dir > $DOCS_SEARCH_DIR > ./docs；
    --db 显式时始终覆盖索引库路径。"""
    ws = resolve_workspace(workspace)
    if ws:
        docs_dir, db_path = resolve_workspace_paths(ws)
        docs_dir, db_path = docs_dir.resolve(), db_path.resolve()
    else:
        docs_dir = resolve_docs_dir(docs_dir_explicit)
        db_path = resolve_db_path(docs_dir)
    if db_explicit:
        db_path = resolve_db_path(docs_dir, db_explicit)
    return docs_dir, db_path


def resolve_db_path(docs_dir, explicit=None):
    """解析索引库路径: --db > $DOCS_SEARCH_DB > ~/.docs-search/<目录哈希>/index.db（按目录隔离）"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get(ENV_DB_PATH):
        return Path(os.environ[ENV_DB_PATH]).expanduser().resolve()
    key = hashlib.sha1(str(docs_dir).replace("\\", "/").lower().encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".docs-search" / key / "index.db"


def meta_path(db_path):
    return db_path.with_name("index.meta.json")


# ============================================================
# 扫描与索引
# ============================================================
def scan_meta(docs_dir):
    """扫描文档目录元信息（不读内容，用于快速变更检测）: [(rel, size, mtime, cat)]"""
    out = []
    if not docs_dir.exists():
        return out
    for root, _, files in os.walk(docs_dir):
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            p = Path(root) / f
            rel = str(p.relative_to(docs_dir)).replace("\\", "/")
            try:
                st = p.stat()
                out.append((rel, st.st_size, int(st.st_mtime), rel.split("/")[0]))
            except OSError:
                pass
    return out


def scan_docs(docs_dir):
    """扫描并读取文档内容: [(rel, cat, title, body, size, mtime)]"""
    out = []
    if not docs_dir.exists():
        return out
    for root, _, files in os.walk(docs_dir):
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            p = Path(root) / f
            rel = str(p.relative_to(docs_dir)).replace("\\", "/")
            try:
                txt = p.read_text(encoding="utf-8")
                lines = txt.split("\n")
                title = next((l[2:].strip() for l in lines if l.startswith("# ")), f)
                bs = next(
                    (i for i, l in enumerate(lines) if l and not l.startswith("#") and not l.startswith(">")),
                    len(lines),
                )
                body = "\n".join(lines[bs:]).strip()
                st = p.stat()
                out.append((rel, rel.split("/")[0], title, body, st.st_size, int(st.st_mtime)))
            except (OSError, UnicodeDecodeError):
                pass  # 跳过不可读/非 UTF-8 文件
    return out


def files_hash(entries):
    """按 (rel, mtime, size) 计算目录指纹，不读内容"""
    h = hashlib.sha256()
    for rel, size, mtime, _cat in sorted(entries, key=lambda x: x[0]):
        h.update(f"{rel}:{mtime}:{size}".encode())
    return h.hexdigest()[:16]


def build_db(db_path):
    """重建数据库表（幂等：DROP IF EXISTS，不依赖删除文件，DB 被占用时也能重建）"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db_path))
    c.execute("DROP TABLE IF EXISTS docs")
    c.execute("CREATE TABLE docs(path TEXT PRIMARY KEY, cat TEXT, title TEXT, body TEXT, size INT, mtime INT)")
    c.execute("CREATE INDEX idx_cat ON docs(cat)")
    c.execute("CREATE INDEX idx_title ON docs(title)")
    c.commit()
    return c


def get_conn(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db_path))
    c.execute("SELECT 1")
    return c


def load_meta(db_path):
    try:
        return json.loads(meta_path(db_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- 元数据损坏时按无元数据处理
        return {}


def save_meta(db_path, meta):
    meta_path(db_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def need_reindex(meta, db_path, docs_dir):
    """需要重建: 无元数据 / 库文件丢失 / 目录指纹变化"""
    if not meta:
        return True
    if not db_path.exists():
        return True
    return files_hash(scan_meta(docs_dir)) != meta.get("hash", "")


def rebuild_index(docs_dir, db_path):
    """全量重建索引，返回 (文档数, 耗时ms)。DB 被占用时抛出带提示的 RuntimeError"""
    t0 = time.time()
    try:
        files = scan_docs(docs_dir)
        c = build_db(db_path)
        c.executemany("INSERT INTO docs VALUES(?,?,?,?,?,?)", files)
        c.commit()
        c.close()
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"索引库被占用，无法重建（是否有其他 docs-search 进程正在使用？）: {e}") from e
    meta = {
        "hash": files_hash(scan_meta(docs_dir)),
        "count": len(files),
        "updated": datetime.now().astimezone().isoformat(),
    }
    save_meta(db_path, meta)
    return len(files), (time.time() - t0) * 1000


def ensure_index(docs_dir, db_path):
    """确保索引存在且最新，返回 (文档数, 是否发生重建)"""
    meta = load_meta(db_path)
    if need_reindex(meta, db_path, docs_dir):
        n, _ = rebuild_index(docs_dir, db_path)
        return n, True
    return meta.get("count", 0), False


# ============================================================
# 库级查询助手（单库搜索/枚举；workspace=all 聚合时复用）
# ============================================================
SNIPPET_LEN = 150       # 兜底/尾部信号摘要长度
SNIPPET_WINDOW = 200    # 命中点居中窗口长度
LOCATE_BODY_CAP = 5120  # 摘要定位仅在前 5KB body 内做（大文档成本控制）
MAX_SEARCH_LIMIT = 50   # web /api/search 单次返回上限（MCP 工具侧另有 20 上限，见 mcp.py）


def round_robin_merge(by_key):
    """按 key 轮转合并各库结果（key 排序 = 轮转顺序，确定性）：
    [a1,b1,c1, a2,b2,c2, ...]，避免聚合截断时单库占满、其他库不可见。"""
    keys = sorted(by_key)
    out, i = [], 0
    while True:
        added = False
        for k in keys:
            seq = by_key[k]
            if i < len(seq):
                out.append(seq[i])
                added = True
        if not added:
            return out
        i += 1


def _mark_keywords(text, kws):
    """在窗口内给能精确定位的关键词加【】标注；按 (位置, 长度降序) 去重叠区间，
    嵌套子串（如 foo/foobar）结果确定。找不到的不标（无害）。"""
    low = text.lower()
    if len(low) != len(text):  # lower 变长（如 İ）：放弃大小写不敏感定位，只做精确匹配
        low = text
    ranges = []
    for kw in kws:
        k = kw.lower() if len(kw.lower()) == len(kw) else kw
        start = 0
        while True:
            i = low.find(k, start)
            if i < 0:
                break
            ranges.append((i, i + len(k)))
            start = i + max(len(k), 1)
    if not ranges:
        return text
    ranges.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged = []
    for s, e in ranges:
        if merged and s < merged[-1][1]:
            continue
        merged.append((s, e))
    out, prev = [], 0
    for s, e in merged:
        out += [text[prev:s], "【", text[s:e], "】"]
        prev = e
    out.append(text[prev:])
    return "".join(out)


def _locate_snippet(title, body, kws):
    """定位锚点生成命中点摘要，返回 (snippet, line)。

    - body 前 5KB 内有关键词命中：以最早命中为锚点取居中窗口，窗口内关键词【】标注，
      line 为锚点行号（1 起）。
    - 仅 title 命中：line=1，摘要取文档头（命中内容即标题，渲染层已单独展示 title）。
    - 命中在 5KB 之后（LIKE 全文命中但定位不到）：尾部信号 "…"+末尾 150 字，line=0，
      不用文档头掩盖"命中在后部"的事实；渲染层对 line=0 省略行号。
    """
    head = body[:LOCATE_BODY_CAP]
    low = head.lower()
    if len(low) != len(head):  # lower 变长：放弃大小写不敏感定位（退化为精确匹配）
        low = head
    pos = -1
    for kw in kws:
        k = kw.lower() if len(kw.lower()) == len(kw) else kw
        i = low.find(k)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos >= 0:
        start = max(0, pos - SNIPPET_WINDOW // 2)
        end = min(len(head), start + SNIPPET_WINDOW)
        start = max(0, end - SNIPPET_WINDOW)  # 边界回缩，窗口尽量取满
        window = _mark_keywords(head[start:end], kws)
        line = head.count("\n", 0, pos) + 1
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body) else ""
        return (prefix + window + suffix).replace("\n", " "), line
    if any(kw.lower() in title.lower() for kw in kws):
        return body[:SNIPPET_LEN].replace("\n", " "), 1
    return "…" + body[-SNIPPET_LEN:].replace("\n", " "), 0


def search_ex(docs_dir, db_path, query, cat=None, limit=20):
    """单库搜索（agent 友好），返回 {"results": [...], "total": int}。

    单条 SQL 完成匹配/计数/排序，无候选池、无二次 COUNT 查询：
    - total = LIKE 全量命中数（COUNT(*) OVER() 先于 LIMIT 求值），无命中时为 0；
    - 排序在 SQL 内：title 命中关键词个数降序（instr/lower 与 LIKE 同为 ASCII 折叠），
      path 升序兜底确定性；
    - lower(?) 写进 SQL、关键词原样绑定——Python 侧 str.lower() 是全 Unicode 折叠，
      会与 SQLite ASCII 折叠错位，不能在 Python 预折。
    results 元素：path/cat/title/snippet/line（line=0 表示行号不可定位）。
    """
    kws = [k for k in query.split() if k]
    if not kws:  # 防御：调用方已挡空串，core 层兜底避免拼出空 WHERE
        return {"results": [], "total": 0}
    ensure_index(docs_dir, db_path)
    c = get_conn(db_path)
    conditions, where_args = [], []
    for kw in kws:
        conditions.append("(title LIKE ? OR body LIKE ?)")
        where_args.extend([f"%{kw}%", f"%{kw}%"])
    title_hits = " + ".join(["(instr(lower(title), lower(?)) > 0)"] * len(kws))
    sql = f"SELECT path, cat, title, body, COUNT(*) OVER() AS total FROM docs WHERE {' AND '.join(conditions)}"
    qargs = list(where_args)
    if cat:
        sql += " AND cat = ?"
        qargs.append(cat)
    sql += f" ORDER BY ({title_hits}) DESC, path ASC LIMIT ?"
    qargs.extend(kws)
    qargs.append(limit)
    rows = c.execute(sql, qargs).fetchall()
    c.close()
    total = rows[0][4] if rows else 0
    results = []
    for p, catv, t, b, _total in rows:
        snippet, line = _locate_snippet(t, b, kws)
        results.append({"path": p, "cat": catv, "title": t, "snippet": snippet, "line": line})
    return {"results": results, "total": total}


def search_lib(docs_dir, db_path, query, cat=None, limit=20):
    """兼容包装：等价 search_ex(...)["results"]（[{path, cat, title, snippet, line}]）。
    公开 API（__init__ 导出）保持原形状；新代码请用 search_ex 获取 total。"""
    return search_ex(docs_dir, db_path, query, cat, limit)["results"]


def render_search_results(query, rows, total, shown, ws=False):
    """搜索结果统一文本渲染（cli/mcp 共用；web 走 JSON 不用）。

    全字段 .get() 容错——旧版远程服务的 results 元素没有 line 等新字段，不得 KeyError。
    渲染层不截断 snippet（服务端已 ≤200 字窗口，截断会把【】切成半边）。
    """
    lines = [f'"{query}" -> {total} matches (showing {shown})' + (" (workspace=all)" if ws else "")]
    for i, r in enumerate(rows, 1):
        tag = f" [{r['ws']}]" if ws and r.get("ws") else ""
        line = r.get("line") or 0
        loc = f" (line {line})" if line else ""
        lines.append(f"{i}. {r.get('path', '')}{tag} | {r.get('title', '')}{loc}")
        snippet = (r.get("snippet") or "").replace("\n", " ")
        if snippet:
            lines.append(f"   {snippet}")
    if total > shown:
        lines.append(f"(还有 {total - shown} 条未显示：提高 limit 或加 cat 过滤)")
    return "\n".join(lines)


def list_lib(docs_dir, db_path, cat=None):
    """枚举单个库，返回 [{path, title, size}]"""
    ensure_index(docs_dir, db_path)
    c = get_conn(db_path)
    if cat:
        rows = c.execute("SELECT path, title, size FROM docs WHERE cat=? ORDER BY title", (cat,)).fetchall()
    else:
        rows = c.execute("SELECT path, title, size FROM docs ORDER BY cat, title").fetchall()
    c.close()
    return [{"path": r[0], "title": r[1], "size": r[2]} for r in rows]


def list_workspaces():
    """扫描已存在的 workspace 库（~/.docs-search/workspaces/ 下子目录），返回排序库名列表"""
    root = Path.home() / ".docs-search" / "workspaces"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and resolve_workspace(p.name))


# ============================================================
# 上传文件名安全化
# ============================================================
def sanitize_filename(name):
    """清洗上传文件名: 仅保留basename、去除危险字符、必须 .md 结尾。非法返回 None"""
    import re

    name = (name or "").strip()
    name = name.replace("\\", "/")
    name = name.split("/")[-1]  # 仅取 basename，防路径穿越
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ")
    if not name or not name.lower().endswith(".md") or len(name) > 200:
        return None
    return name


def dedupe_target(docs_dir, name):
    """在 docs_dir/uploads/ 下生成不重名的目标路径（if_exists=keep 的内部实现）"""
    updir = docs_dir / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    target = updir / name
    if not target.exists():
        return target
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        cand = updir / f"{stem}-{i}{ext}"
        if not cand.exists():
            return cand
    return None


def resolve_upload_target(docs_dir, name, if_exists="error"):
    """按同名策略分配上传目标，返回 (Path|None, str|None): (目标路径, None) 或 (None, 错误消息)。

    if_exists: error（默认，重名提示不写）> overwrite（强制覆盖原文件）> keep（生成 -N 新文件）
    """
    if if_exists not in IF_EXISTS_CHOICES:
        return None, f"未知 if_exists 取值: {if_exists!r}（可选: error/overwrite/keep）"
    updir = docs_dir / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    target = updir / name
    if not target.exists():
        return target, None
    if if_exists == "overwrite":
        return target, None
    if if_exists == "keep":
        t = dedupe_target(docs_dir, name)
        if t is None:
            return None, "无法分配目标文件名（同名变体已满 999）"
        return t, None
    return None, f"文件已存在: uploads/{name}（同名冲突——传 if_exists=overwrite 覆盖,或 if_exists=keep 生成 -N 新文件）"
