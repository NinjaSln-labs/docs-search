"""docs_search.core 单元测试"""

import sqlite3
from pathlib import Path

import pytest

from docs_search import core


# ============================================================
# sanitize_filename（上传安全）
# ============================================================
class TestSanitizeFilename:
    def test_valid(self):
        assert core.sanitize_filename("notes.md") == "notes.md"

    def test_unicode_kept(self):
        assert core.sanitize_filename("中文文档.md") == "中文文档.md"

    @pytest.mark.parametrize(
        "bad",
        [
            "../evil.md",  # 路径穿越
            "..\\evil.md",  # Windows 路径穿越
            "a/b/c.md",  # 多级路径 → basename
            "notes.txt",  # 非 .md
            "notes",  # 无扩展名
            "",  # 空
            None,  # None
            ".md",  # 只有扩展名
            "x" * 250 + ".md",  # 超长
        ],
    )
    def test_rejected_or_sanitized(self, bad):
        result = core.sanitize_filename(bad)
        if result is not None:
            # 若放行，绝不包含路径分隔符且必为 .md
            assert "/" not in result and "\\" not in result
            assert result.lower().endswith(".md")

    def test_traversal_isolated(self):
        """穿越路径必须被裁剪为纯文件名"""
        assert core.sanitize_filename("../evil.md") == "evil.md"
        assert core.sanitize_filename("..\\..\\etc\\passwd.md") == "passwd.md"


# ============================================================
# 路径解析（不绑定本地路径）
# ============================================================
class TestPathResolution:
    def test_explicit_dir_wins(self, tmp_path):
        d = core.resolve_docs_dir(str(tmp_path))
        assert d == tmp_path.resolve()

    def test_env_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCS_SEARCH_DIR", str(tmp_path))
        assert core.resolve_docs_dir() == tmp_path.resolve()

    def test_default_cwd_docs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOCS_SEARCH_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert core.resolve_docs_dir() == (tmp_path / "docs").resolve()

    def test_db_isolated_per_dir(self, tmp_path, monkeypatch):
        """不同文档目录 → 不同索引库（哈希隔离）"""
        monkeypatch.delenv("DOCS_SEARCH_DB", raising=False)
        d1, d2 = tmp_path / "a", tmp_path / "b"
        p1 = core.resolve_db_path(d1)
        p2 = core.resolve_db_path(d2)
        assert p1 != p2
        assert p1.parent.name == p2.parent.name.split(".")[0] or p1 != p2
        assert ".docs-search" in str(p1)

    def test_db_explicit(self, tmp_path):
        db = tmp_path / "x" / "my.db"
        assert core.resolve_db_path(tmp_path, str(db)) == db.resolve()

    def test_no_hardcoded_paths(self):
        """源码不得包含任何个人/本机绝对路径绑定"""
        src = Path(core.__file__).parent
        for py in src.glob("*.py"):
            text = py.read_text(encoding="utf-8").lower()
            for token in ("piworkspace", "ninjasin-labs", "c:\\users\\", "/users/", "e:\\"):
                assert token not in text, f"{py.name} 含本地路径残留: {token}"


# ============================================================
# 扫描、索引、重建
# ============================================================
@pytest.fixture
def corpus(tmp_path):
    docs = tmp_path / "docs"
    (docs / "code").mkdir(parents=True)
    (docs / "code" / "alpha.md").write_text("# Alpha\n\nbody with keyword ZEBRA\n", encoding="utf-8")
    (docs / "code" / "beta.md").write_text("# Beta\n\nanother file\n", encoding="utf-8")
    (docs / "notes.txt").write_text("not markdown", encoding="utf-8")
    return docs


class TestIndexLifecycle:
    def test_scan_docs(self, corpus):
        files = core.scan_docs(corpus)
        rels = {f[0] for f in files}
        assert rels == {"code/alpha.md", "code/beta.md"}  # 只收 .md
        alpha = next(f for f in files if f[0] == "code/alpha.md")
        assert alpha[1] == "code" and alpha[2] == "Alpha" and "ZEBRA" in alpha[3]

    def test_rebuild_and_search(self, corpus, tmp_path):
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        n, dt = core.rebuild_index(corpus, db)
        assert n == 2 and dt >= 0
        c = core.get_conn(db)
        rows = c.execute("SELECT path FROM docs WHERE body LIKE '%ZEBRA%'").fetchall()
        c.close()
        assert rows == [("code/alpha.md",)]

    def test_rebuild_idempotent(self, corpus, tmp_path):
        """重复重建不报错（DROP TABLE IF EXISTS）"""
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        core.rebuild_index(corpus, db)
        n, _ = core.rebuild_index(corpus, db)
        assert n == 2

    def test_ensure_index_detects_change(self, corpus, tmp_path):
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        n, rebuilt = core.ensure_index(corpus, db)
        assert n == 2 and rebuilt
        n, rebuilt = core.ensure_index(corpus, db)
        assert n == 2 and not rebuilt  # 未变更不重建
        (corpus / "code" / "gamma.md").write_text("# Gamma\n", encoding="utf-8")
        n, rebuilt = core.ensure_index(corpus, db)
        assert n == 3 and rebuilt  # 变更触发重建

    def test_db_missing_triggers_rebuild(self, corpus, tmp_path):
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        core.rebuild_index(corpus, db)
        db.unlink()  # 库丢失但 meta 还在 → 必须重建
        n, rebuilt = core.ensure_index(corpus, db)
        assert n == 2 and rebuilt

    def test_meta_roundtrip(self, corpus, tmp_path):
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        core.rebuild_index(corpus, db)
        meta = core.load_meta(db)
        assert meta["count"] == 2 and len(meta["hash"]) == 16

    def test_locked_db_clear_error(self, corpus, tmp_path):
        """库被写锁占用时报业务错误而非裸 sqlite3 异常"""
        db = core.resolve_db_path(corpus, str(tmp_path / "idx.db"))
        core.rebuild_index(corpus, db)
        blocker = sqlite3.connect(str(db))
        blocker.execute("INSERT INTO docs VALUES('hold.md','x','t','b',1,0)")  # 未提交写事务 → 持锁
        try:
            with pytest.raises(RuntimeError, match="占用"):
                core.rebuild_index(corpus, db)
        finally:
            blocker.rollback()
            blocker.close()


# ============================================================
# 上传目标分配
# ============================================================
class TestDedupeTarget:
    def test_first_take(self, tmp_path):
        t = core.dedupe_target(tmp_path, "a.md")
        assert t == tmp_path / "uploads" / "a.md"

    def test_collision_deduped(self, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.md").write_text("x", encoding="utf-8")
        t = core.dedupe_target(tmp_path, "a.md")
        assert t.name == "a-1.md"
