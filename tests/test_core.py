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


class TestUploadTarget:
    """同名冲突策略: error(默认提示) / overwrite(覆盖) / keep(-N 新文件)"""

    def test_first_take_any_policy(self, tmp_path):
        for policy in ("error", "overwrite", "keep"):
            t, msg = core.resolve_upload_target(tmp_path, "fresh.md", policy)
            assert t == tmp_path / "uploads" / "fresh.md" and msg is None

    def test_default_conflict_returns_hint(self, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.md").write_text("old", encoding="utf-8")
        t, msg = core.resolve_upload_target(tmp_path, "a.md")  # 默认 error
        assert t is None and "文件已存在" in msg and "overwrite" in msg
        assert (tmp_path / "uploads" / "a.md").read_text(encoding="utf-8") == "old"  # 未写

    def test_overwrite_returns_same_target(self, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.md").write_text("old", encoding="utf-8")
        t, msg = core.resolve_upload_target(tmp_path, "a.md", "overwrite")
        assert t == tmp_path / "uploads" / "a.md" and msg is None

    def test_keep_returns_suffixed(self, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.md").write_text("x", encoding="utf-8")
        t, msg = core.resolve_upload_target(tmp_path, "a.md", "keep")
        assert t.name == "a-1.md" and msg is None

    def test_unknown_policy_rejected(self, tmp_path):
        t, msg = core.resolve_upload_target(tmp_path, "a.md", "bogus")
        assert t is None and "未知 if_exists" in msg


class TestWorkspace:
    """操作级 workspace: 自包含库 ~/.docs-search/workspaces/<ws>/（docs 目录 + index.db）"""

    def test_default_none(self):
        assert core.resolve_workspace(None) is None
        assert core.resolve_workspace("") is None

    def test_explicit_kept(self):
        assert core.resolve_workspace("proj-a") == "proj-a"
        assert core.resolve_workspace(" proj-a ") == "proj-a"

    def test_unsafe_chars_sanitized(self):
        assert core.resolve_workspace("../etc/passwd") == "_etc_passwd"
        assert core.resolve_workspace("a\\b: c") == "a_b_ c"

    def test_self_contained_paths(self):
        docs, db = core.resolve_workspace_paths("proj-a")
        parts = docs.parts
        assert "workspaces" in parts and "proj-a" in parts and parts[-1] == "docs"
        dparts = db.parts
        assert "workspaces" in dparts and "proj-a" in dparts and dparts[-1] == "index.db"

    def test_workspace_ignores_dir_explicit(self, tmp_path):
        """workspace 优先: 指定后 --dir 被忽略,库自包含"""
        docs, db = core.resolve_paths(tmp_path / "elsewhere", workspace="proj-a")
        assert "workspaces" in docs.parts and docs.parts[-2] == "proj-a"
        assert "workspaces" in db.parts and db.parts[-2] == "proj-a"

    def test_default_paths_unchanged(self, tmp_path, monkeypatch):
        """不传 workspace → 默认规则完全不变（旧版兼容）"""
        monkeypatch.delenv("DOCS_SEARCH_DB", raising=False)
        docs, db = core.resolve_paths(tmp_path)
        assert docs == core.resolve_docs_dir(tmp_path)
        assert db == core.resolve_db_path(docs)

    def test_workspaces_isolated(self):
        d1, db1 = core.resolve_workspace_paths("w1")
        d2, db2 = core.resolve_workspace_paths("w2")
        assert d1 != d2 and db1 != db2

    def test_db_explicit_overrides_workspace(self, tmp_path):
        docs, db = core.resolve_paths(tmp_path, db_explicit=str(tmp_path / "x.db"), workspace="proj-a")
        assert db == (tmp_path / "x.db").resolve()
        assert "workspaces" in docs.parts and docs.parts[-2] == "proj-a"


# ============================================================
# search_ex / render_search_results（agent 友好搜索）
# ============================================================
@pytest.fixture
def search_corpus(tmp_path):
    """多文档语料：title 命中/正文命中/大小写/长文档尾部命中"""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "title-hit.md").write_text("# 部署指南\n\n正文无关键词内容。\n", encoding="utf-8")
    (docs / "body-hit.md").write_text("# 其他\n\n" + " filler\n" * 20 + "这里讲 deployment 部署流程。\n", encoding="utf-8")
    (docs / "case.md").write_text("# Case\n\nconfig lives in token store\n", encoding="utf-8")
    (docs / "long.md").write_text("# Long\n\n" + "pad\n" * 4000 + "尾部 deployment 命中\n", encoding="utf-8")
    return docs


@pytest.fixture
def search_db(search_corpus, tmp_path):
    db = core.resolve_db_path(search_corpus, str(tmp_path / "idx.db"))
    core.rebuild_index(search_corpus, db)
    return db


class TestSearchEx:
    def test_total_counts_all_matches_beyond_limit(self, search_corpus, search_db):
        data = core.search_ex(search_corpus, search_db, "deployment", limit=1)
        assert data["total"] == 2  # body-hit + long（title-hit 只命中中文标题不命中 deployment）
        assert len(data["results"]) == 1  # LIMIT 截断

    def test_empty_result(self, search_corpus, search_db):
        data = core.search_ex(search_corpus, search_db, "nonexistent-xyz")
        assert data == {"results": [], "total": 0}

    def test_empty_query_guard(self, search_corpus, search_db):
        assert core.search_ex(search_corpus, search_db, "   ") == {"results": [], "total": 0}

    def test_ranking_title_hit_first_then_path(self, search_corpus, search_db):
        """title 命中排在纯正文命中前；同分按 path 升序（确定性）"""
        (search_corpus / "a-title.md").write_text("# deployment notes\n\n无正文命中\n", encoding="utf-8")
        (search_corpus / "z-title.md").write_text("# deployment too\n\n无正文命中\n", encoding="utf-8")
        core.rebuild_index(search_corpus, search_db)
        data = core.search_ex(search_corpus, search_db, "deployment", limit=10)
        paths = [r["path"] for r in data["results"]]
        assert paths[0].endswith("a-title.md") and paths[1].endswith("z-title.md")

    def test_match_centered_snippet_cjk(self, search_corpus, search_db):
        """摘要以命中点为中心，含 CJK，关键词带【】标注"""
        data = core.search_ex(search_corpus, search_db, "部署流程", limit=1)
        r = data["results"][0]
        assert r["path"] == "body-hit.md"
        assert "【部署流程】" in r["snippet"] and "这里讲" in r["snippet"]
        assert r["line"] > 1

    def test_case_insensitive_locate(self, search_corpus, search_db):
        """query 大写 TOKEN 命中正文小写 token：SQL LIKE/SQL 排序/Python 定位同一语义"""
        data = core.search_ex(search_corpus, search_db, "TOKEN", limit=1)
        r = data["results"][0]
        assert r["path"] == "case.md"
        assert "【token】" in r["snippet"]  # Python 侧用原文小写标注
        assert r["line"] == 1

    def test_title_only_hit(self, search_corpus, search_db):
        """仅标题命中：line=1，摘要为文档头"""
        data = core.search_ex(search_corpus, search_db, "部署指南", limit=1)
        r = data["results"][0]
        assert r["path"] == "title-hit.md"
        assert r["line"] == 1
        assert "正文无关键词" in r["snippet"]

    def test_tail_signal_when_hit_beyond_locate_cap(self, search_corpus, search_db):
        """命中位于 5KB 之后：尾部信号（…"开头），不掩盖命中在后部；line=0"""
        data = core.search_ex(search_corpus, search_db, "尾部", limit=1)
        r = data["results"][0]
        assert r["path"] == "long.md"
        assert r["snippet"].startswith("…") and "deployment" in r["snippet"]
        assert r["line"] == 0

    def test_search_lib_wrapper_shape(self, search_corpus, search_db):
        rows = core.search_lib(search_corpus, search_db, "deployment", limit=5)
        assert isinstance(rows, list) and {"path", "cat", "title", "snippet", "line"} <= set(rows[0])


class TestRoundRobinMerge:
    def test_interleaves_and_deterministic(self):
        merged = {"ws-b": ["b1", "b2", "b3"], "ws-a": ["a1", "a2"]}
        assert core.round_robin_merge(merged) == ["a1", "b1", "a2", "b2", "b3"]
        # 轮转序 = key 排序：同输入同输出
        assert core.round_robin_merge({"ws-b": ["b1"], "ws-a": ["a1"]}) == ["a1", "b1"]

    def test_single_lib_does_not_starve_others_after_truncate(self):
        merged = {"default": list("abcdefghij"), "wa": ["x1", "x2"]}
        rows = core.round_robin_merge(merged)[:3]
        assert rows == ["a", "x1", "b"]  # wa 未被 default 挤出


class TestRenderSearchResults:
    def test_format_and_truncation_hint(self):
        rows = [{"path": "a.md", "title": "A", "snippet": "【kw】 hit", "line": 3, "ws": "wa"}]
        out = core.render_search_results("kw", rows, 9, 1, ws=True)
        assert '"kw" -> 9 matches (showing 1) (workspace=all)' in out
        assert "1. a.md [wa] | A (line 3)" in out
        assert "【kw】 hit" in out
        assert "还有 8 条未显示" in out

    def test_tolerates_missing_fields(self):
        """旧服务端元素无 line/ws/snippet：不 KeyError，行号省略"""
        rows = [{"path": "a.md", "title": "A"}]
        out = core.render_search_results("kw", rows, 1, 1)
        assert "1. a.md | A" in out and "(line" not in out
        assert "还有" not in out  # total == shown 无截断提示

    def test_line_zero_omitted(self):
        rows = [{"path": "a.md", "title": "A", "snippet": "…tail", "line": 0}]
        out = core.render_search_results("kw", rows, 1, 1)
        assert "(line" not in out
