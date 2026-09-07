"""docs_search: 零依赖本地文档搜索引擎（SQLite 索引，CLI + Web UI）"""

__version__ = "1.1.0"

from .core import (  # noqa: F401
    ENV_DB_PATH,
    ENV_DOCS_DIR,
    ENV_SERVICE_URL,
    MAX_UPLOAD_BYTES,
    build_db,
    dedupe_target,
    ensure_index,
    files_hash,
    get_conn,
    load_meta,
    meta_path,
    need_reindex,
    rebuild_index,
    resolve_db_path,
    resolve_docs_dir,
    resolve_service_url,
    sanitize_filename,
    save_meta,
    scan_docs,
    scan_meta,
    win_utf8,
)
