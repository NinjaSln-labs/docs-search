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
