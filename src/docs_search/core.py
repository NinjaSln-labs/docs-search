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

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import threading
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
INDEX_CHECK_TTL = 30.0  # 秒：目录指纹复核的最小间隔（避免每次请求全目录 stat）
REBUILD_RETRY = 3       # 索引库被占用时的重试次数
REBUILD_BACKOFF = 0.2   # 重试退避基数（秒，按 2 的幂递增）


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


def doc_entry(docs_dir, rel):
    """单篇文档的索引行 (rel, cat, title, body, size, mtime)。
    文件不存在/不可读返回 None（删除场景用）。rel 为相对文档根的 POSIX 路径。"""
    p = Path(docs_dir) / rel
    try:
        txt = p.read_text(encoding="utf-8")
        st = p.stat()
    except (OSError, UnicodeDecodeError):
        return None
    lines = txt.split("\n")
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), p.name)
    bs = next(
        (i for i, l in enumerate(lines) if l and not l.startswith("#") and not l.startswith(">")),
        len(lines),
    )
    body = "\n".join(lines[bs:]).strip()
    return (rel, rel.split("/")[0], title, body, st.st_size, int(st.st_mtime))


def files_hash(entries):
    """按 (rel, mtime, size) 计算目录指纹，不读内容"""
    h = hashlib.sha256()
    for rel, size, mtime, _cat in sorted(entries, key=lambda x: x[0]):
        h.update(f"{rel}:{mtime}:{size}".encode())
    return h.hexdigest()[:16]


def files_map(entries):
    """(rel, size, mtime, cat) 列表 → {rel: [mtime, size]}（与 files_hash 同序同格式）"""
    return {rel: [int(mtime), int(size)] for rel, size, mtime, _cat in entries}


def map_hash(fmap):
    """按已知的 {rel: [mtime, size]} 计算目录指纹——不做 stat，仅哈希（O(N) 字符串、零系统调用）。
    与 files_hash(scan_meta(...)) 同值，故可直接与 meta["hash"] 比较。"""
    h = hashlib.sha256()
    for rel in sorted(fmap):
        mtime, size = fmap[rel]
        h.update(f"{rel}:{mtime}:{size}".encode())
    return h.hexdigest()[:16]


_INDEX_LOCKS = {}  # db_path -> 进程内互斥锁（序列化同一库的索引写）
_INDEX_LOCKS_GUARD = threading.Lock()


def _index_lock(db_path):
    """取某库的进程内互斥锁（可重入：增量更新回落到全量重建时不会自锁）。
    作用：上传/删除与重建并发时不再互相撞 SQLite 锁。"""
    key = str(db_path)
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _INDEX_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _rebuild_file_lock(db_path, timeout=60.0):
    """跨进程重建互斥（POSIX 用 fcntl.flock；Windows 退化为原子创建锁文件）。
    超时抛 RuntimeError——不再无限等待，也不直接暴露 SQLite 的「库被占用」。"""
    lock_path = db_path.with_name(db_path.name + ".rebuild.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 -- 句柄需跨越 yield 持有
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise RuntimeError("索引重建等待超时（另一个进程正在重建索引）")
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        return
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() >= deadline:
                raise RuntimeError("索引重建等待超时（另一个进程正在重建索引）")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


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
    """全量重建索引，返回 (文档数, 耗时ms)。
    跨进程互斥 + 库被占用时退避重试：并发上传/重建不再直接抛「索引库被占用」。"""
    t0 = time.time()
    with _index_lock(db_path), _rebuild_file_lock(db_path):
        last_error = None
        files = []
        for attempt in range(REBUILD_RETRY):
            c = None
            try:
                files = scan_docs(docs_dir)
                c = build_db(db_path)
                c.executemany("INSERT INTO docs VALUES(?,?,?,?,?,?)", files)
                c.commit()
                c.close()
                c = None
                break
            except sqlite3.OperationalError as e:
                last_error = e
                if c is not None:
                    try:
                        c.close()
                    except sqlite3.Error:
                        pass
                if attempt < REBUILD_RETRY - 1:
                    time.sleep(REBUILD_BACKOFF * (2 ** attempt))
        else:
            raise RuntimeError(
                f"索引库被占用，无法重建（是否有其他 docs-search 进程正在使用？）: {last_error}"
            ) from last_error
    entries = scan_meta(docs_dir)
    meta = {
        "hash": files_hash(entries),
        "files": files_map(entries),
        "count": len(files),
        "updated": datetime.now().astimezone().isoformat(),
        "checked": time.time(),
    }
    save_meta(db_path, meta)
    return len(files), (time.time() - t0) * 1000


def ensure_index(docs_dir, db_path):
    """确保索引存在且最新，返回 (文档数, 是否发生重建)。
    目录指纹复核按 TTL 节流（默认 30s）：高频请求不再每次全目录 stat；
    复核到指纹变化才重建（重建带互斥与退避重试，不因并发而直接失败）。"""
    meta = load_meta(db_path)
    if not meta or not db_path.exists() or "files" not in meta:
        # 无元数据 / 库文件丢失 / 旧版元数据（无 files 映射）→ 重建一次并补齐 files
        n, _ = rebuild_index(docs_dir, db_path)
        return n, True
    checked = float(meta.get("checked") or 0)
    if time.time() - checked >= INDEX_CHECK_TTL:
        if files_hash(scan_meta(docs_dir)) != meta.get("hash", ""):
            n, _ = rebuild_index(docs_dir, db_path)
            return n, True
        meta = dict(meta)
        meta["checked"] = time.time()
        save_meta(db_path, meta)
    return int(meta.get("count", 0)), False


def update_index_entry(docs_dir, db_path, rel):
    """增量更新单篇文档的索引行并刷新目录指纹——上传/删除后调用，避免全量重建。
    目录指纹由已知的 {rel: [mtime, size]} 映射重算（不再全目录 stat），
    批量上传的指纹开销降为 O(N) 哈希、零系统调用。
    返回 (文档数, 是否走了全量重建)。索引缺失/表不存在时退化为全量重建。"""
    meta = load_meta(db_path)
    if not db_path.exists() or not meta or not _has_docs_table(db_path) or "files" not in meta:
        n, _ = rebuild_index(docs_dir, db_path)
        return n, True
    with _index_lock(db_path):
        try:
            c = get_conn(db_path)
        except sqlite3.OperationalError:
            n, _ = rebuild_index(docs_dir, db_path)
            return n, True
        row = None
        try:
            row = doc_entry(docs_dir, rel)
            if row is None:
                c.execute("DELETE FROM docs WHERE path=?", (rel,))
            else:
                c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)", row)
            c.commit()
            n = c.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        except sqlite3.OperationalError:
            c.close()
            n, _ = rebuild_index(docs_dir, db_path)
            return n, True
        finally:
            try:
                c.close()
            except sqlite3.Error:  # 已关闭时忽略
                pass
        fmap = {k: list(v) for k, v in (meta.get("files") or {}).items()}
        if row is None:
            fmap.pop(rel, None)
        else:
            fmap[rel] = [int(row[5]), int(row[4])]
        meta = dict(meta)
        meta["files"] = fmap
        meta["hash"] = map_hash(fmap)
        meta["count"] = n
        meta["updated"] = datetime.now().astimezone().isoformat()
        meta["checked"] = time.time()
        save_meta(db_path, meta)
    return n, False


def _has_docs_table(db_path):
    """索引库里是否已有 docs 表（增量更新的前置条件）"""
    try:
        c = get_conn(db_path)
    except sqlite3.Error:  # 库不可用时按无表处理
        return False
    try:
        row = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='docs'"
        ).fetchone()
        return row is not None
    finally:
        c.close()


# ============================================================
# 库级查询助手（单库搜索/枚举；workspace=all 聚合时复用）
# ============================================================
SNIPPET_LEN = 150       # 兜底/尾部信号摘要长度
SNIPPET_WINDOW = 200    # 命中点居中窗口长度
LOCATE_BODY_CAP = 5120  # 摘要定位仅在前 5KB body 内做（大文档成本控制）
MAX_SEARCH_LIMIT = 50   # web /api/search 单次返回上限（MCP 工具侧另有 20 上限，见 mcp.py）
ENV_MAX_SEARCH_LIMIT = "DOCS_SEARCH_MAX_SEARCH_LIMIT"  # 环境变量覆盖单次返回上限
MATCH_MODES = ("and", "or")  # 关键词组合语义：and=全部命中 / or=任一命中


def resolve_max_search_limit(default=MAX_SEARCH_LIMIT):
    """单次搜索返回上限：环境变量 > 默认值（非法或非正数时回落默认）"""
    raw = (os.environ.get(ENV_MAX_SEARCH_LIMIT) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return default
        if value >= 1:
            return value
    return default


def _is_cjk_char(ch):
    """是否 CJK 汉字（基本区 / 扩展 A / 兼容区 / 扩展 B+）"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
    )


def _has_cjk(text):
    return any(_is_cjk_char(ch) for ch in text)


def _bigrams(token):
    """CJK 词的二元组切分（整串未出现时的子串退化召回用）"""
    return [token[i:i + 2] for i in range(len(token) - 1)]


def _query_groups(query):
    """按空白切词 → 候选关键词组。
    返回 (strict, relaxed)：strict 每组只有原词；relaxed 对 CJK 长词追加二元组
    （原词整串未出现时，仍能按子串召回；非 CJK 词不变）。"""
    tokens = [k for k in query.split() if k]
    strict = [[t] for t in tokens]
    relaxed = []
    for token in tokens:
        alts = [token]
        if _has_cjk(token) and len(token) >= 3:
            alts.extend(_bigrams(token))
        relaxed.append(alts)
    return strict, relaxed


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


def _run_search_query(db_path, groups, op, limit, cat=None):
    """执行一次检索：单条 SQL 完成匹配/计数/排序，返回 (rows, total)。
    op="AND"：所有关键词组都要命中；op="OR"：任一组命中即可（按命中组数降序）。
    参数用命名占位符（同名可复用），避免为排序表达式重复绑定同一关键词。"""
    conditions, params, title_exprs = [], {}, []
    for gi, alts in enumerate(groups):
        parts = []
        for ai, alt in enumerate(alts):
            like_name = f"g{gi}_{ai}"
            instr_name = f"t{gi}_{ai}"
            parts.append(f"(title LIKE :{like_name} OR body LIKE :{like_name})")
            params[like_name] = f"%{alt}%"
            params[instr_name] = alt
            title_exprs.append(f"(instr(lower(title), lower(:{instr_name})) > 0)")
        conditions.append("(" + " OR ".join(parts) + ")")
    where = f" {op} ".join(conditions)
    order = []
    if op == "OR":
        order.append("(" + " + ".join(f"(CASE WHEN {c} THEN 1 ELSE 0 END)" for c in conditions) + ") DESC")
    order.append("(" + " + ".join(title_exprs) + ") DESC")
    order.append("path ASC")
    sql = "SELECT path, cat, title, body, COUNT(*) OVER() AS total FROM docs WHERE " + where
    if cat:
        sql += " AND cat = :cat"
        params["cat"] = cat
    sql += " ORDER BY " + ", ".join(order) + " LIMIT :limit"
    params["limit"] = limit
    c = get_conn(db_path)
    try:
        rows = c.execute(sql, params).fetchall()
    finally:
        c.close()
    total = rows[0][4] if rows else 0
    return rows, total


def search_ex(docs_dir, db_path, query, cat=None, limit=20, mode="and"):
    """单库搜索（agent 友好），返回
    {"results": [...], "total": int, "returned": int, "truncated": bool,
      "mode": str, "relaxed": bool}。

    关键词语义（空格切词）：
    - mode="and"（默认）：所有词都要命中（AND）。严格 AND 0 命中时，对 CJK 长词做
      二元组退化重试——整串未出现也能按子串召回（严格 AND 有命中时条数与排序不变，
      向后兼容）；退化只在严格 0 命中时发生，响应用 relaxed=true 标注。
    - mode="or"：任一词命中即可，按命中组数降序、标题命中数降序、路径升序。

    单条 SQL 完成匹配/计数/排序，无候选池、无二次 COUNT 查询：
    - total = LIKE 全量命中数（COUNT(*) OVER() 先于 LIMIT 求值），无命中时为 0；
    - 排序在 SQL 内：title 命中关键词个数降序（instr/lower 与 LIKE 同为 ASCII 折叠），
      path 升序兜底确定性；
    - lower(...) 写进 SQL、关键词原样绑定——Python 侧 str.lower() 是全 Unicode 折叠，
      会与 SQLite ASCII 折叠错位，不能在 Python 预折。
    results 元素：path/cat/title/snippet/line（line=0 表示行号不可定位）。
    """
    strict, relaxed_groups = _query_groups(query)
    if not strict:  # 防御：调用方已挡空串，core 层兜底避免拼出空 WHERE
        return {"results": [], "total": 0, "returned": 0, "truncated": False,
                "mode": mode or "and", "relaxed": False}
    mode = (mode or "and").lower()
    if mode not in MATCH_MODES:
        mode = "and"
    ensure_index(docs_dir, db_path)
    # 默认：严格 AND →（0 命中时）CJK 二元组退化 AND；mode=or：直接 OR 召回
    plan = [("OR", relaxed_groups)] if mode == "or" else [("AND", strict), ("AND", relaxed_groups)]
    rows, total, used_groups, relaxed = [], 0, plan[0][1], False
    for op, groups in plan:
        rows, total = _run_search_query(db_path, groups, op, limit, cat)
        used_groups = groups
        if total > 0:
            break
    # relaxed 仅在真的用了 CJK 二元组退化时为真（非 CJK 查询的退化与严格等价）
    relaxed = (
        mode == "and"
        and used_groups is not strict
        and any(len(group) > 1 for group in used_groups)
    )
    kws = [alt for group in used_groups for alt in group]
    results = []
    for p, catv, t, b, _total in rows:
        snippet, line = _locate_snippet(t, b, kws)
        results.append({"path": p, "cat": catv, "title": t, "snippet": snippet, "line": line})
    return {
        "results": results,
        "total": total,
        "returned": len(results),
        "truncated": total > len(results),
        "mode": mode,
        "relaxed": relaxed,
    }


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
