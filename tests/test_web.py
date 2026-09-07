"""Web API 测试（内存 HTTP 服务 + urllib）"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from docs_search.web import AuthConfig, make_handler, validate_listen_config


def _urlopen_retry(req, attempts=3):
    """Windows 安全软件会间歇性指断 localhost 回环连接（WinError 10053/10054），
    对连接层错误做短重试；HTTPError（业务状态码）不重试。"""
    last = None
    for i in range(attempts):
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError:
            raise
        except (ConnectionAbortedError, ConnectionResetError, urllib.error.URLError) as e:
            last = e
            time.sleep(0.05 * (i + 1))
    raise last


@pytest.fixture
def server(tmp_path):
    docs = tmp_path / "docs"
    (docs / "code").mkdir(parents=True)
    (docs / "code" / "a.md").write_text("# A\n\nbody TOKEN_ONE\n", encoding="utf-8")
    db = tmp_path / "idx.db"
    srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", docs
    srv.shutdown()


@pytest.fixture
def server_auth(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "code").mkdir(parents=True)
    (docs / "code" / "a.md").write_text("# A\n\nbody TOKEN_ONE\n", encoding="utf-8")
    db = tmp_path / "idx.db"
    srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db, AuthConfig(token="s3cret-token")))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", docs
    srv.shutdown()


def get(base, path):
    with _urlopen_retry(base + path) as r:
        return json.loads(r.read().decode("utf-8"))


def req(base, path, headers=None):
    r = urllib.request.Request(base + path, headers=headers or {})
    try:
        with _urlopen_retry(r) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


class TestWorkspaceAPI:
    """操作级 workspace: 请求带 ?ws=<name> 动态切库,不传 = 默认库"""

    def test_dynamic_isolation(self, server, tmp_path, monkeypatch):
        fake_home = tmp_path / "fakehome"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        base, _ = server
        # 默认库: 上传一个文档
        post(base, "/api/upload?filename=shared.md", "default-content", raw=True)
        assert get(base, "/api/stats")["count"] == 2  # code/a.md + shared.md
        # workspace 库: 同名上传独立,互不可见
        r = post(base, "/api/upload?filename=shared.md&ws=proj-a", "proj-a-content", raw=True)
        assert r["ok"]
        # 默认库搜不到 ws 内容,ws 库能搜到
        assert get(base, "/api/search?q=proj-a-content")["results"] == []
        hits = get(base, "/api/search?q=proj-a-content&ws=proj-a")
        assert hits["results"][0]["path"] == "uploads/shared.md"
        # stats 区分两库
        assert get(base, "/api/stats")["count"] == 2
        assert get(base, "/api/stats?ws=proj-a")["count"] == 1

    def test_workspace_delete_isolated(self, server, tmp_path, monkeypatch):
        fake_home = tmp_path / "fakehome"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        base, _ = server
        post(base, "/api/upload?filename=a.md&ws=ws1", "one", raw=True)
        post(base, "/api/upload?filename=a.md&ws=ws2", "two", raw=True)
        # 只删 ws1 的,ws2 不受影响
        r = post(base, "/api/delete?path=uploads/a.md&ws=ws1")
        assert r["ok"]
        assert get(base, "/api/stats?ws=ws1")["count"] == 0
        assert get(base, "/api/stats?ws=ws2")["count"] == 1
    def test_no_credentials_rejected(self, server_auth):
        base, _ = server_auth
        status, _ = req(base, "/api/stats")
        assert status == 401

    def test_bearer_granted(self, server_auth):
        base, _ = server_auth
        status, body = req(base, "/api/stats", headers={"Authorization": "Bearer s3cret-token"})
        assert status == 200 and '"count": 1' in body

    def test_wrong_token_rejected(self, server_auth):
        base, _ = server_auth
        status, _ = req(base, "/api/stats", headers={"Authorization": "Bearer wrong"})
        assert status == 401

    def test_post_endpoints_protected_too(self, server_auth):
        base, _ = server_auth
        status, _ = req(base, "/api/upload?filename=x.md")
        assert status == 401
        status, _ = req(base, "/api/delete?path=uploads/x.md")
        assert status == 401

    def test_basic_auth(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
        db = tmp_path / "idx.db"
        import base64 as b64

        srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db, AuthConfig(username="admin", password="pw")))
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            status, _ = req(base, "/api/stats")
            assert status == 401  # 未带凭据拒绝
            good = "Basic " + b64.b64encode(b"admin:pw").decode()
            status, body = req(base, "/api/stats", headers={"Authorization": good})
            assert status == 200 and '"count": 1' in body
            bad = "Basic " + b64.b64encode(b"admin:wrong").decode()
            status, _ = req(base, "/api/stats", headers={"Authorization": bad})
            assert status == 401
        finally:
            srv.shutdown()

    def test_subset_credentials_config_rejected(self):
        """user 无 password / password 无 user 均不构成有效认证"""
        assert AuthConfig(token=None, username="u", password=None).enabled is False
        assert AuthConfig(token="t", username="u", password="p").enabled is True

    def test_loopback_guard(self):
        """回环地址无需认证；非回环无认证被拒——DB 泄露红线"""
        assert validate_listen_config("127.0.0.1", AuthConfig()) is None
        assert validate_listen_config("localhost", AuthConfig()) is None
        assert validate_listen_config("0.0.0.0", AuthConfig()) is not None
        assert validate_listen_config("0.0.0.0", AuthConfig(token="t")) is None
        assert validate_listen_config("192.168.1.5", AuthConfig()) is not None


class TestSQLInjection:
    """SQL 注入防护证明: 所有查询均为参数化,恶意输入按字面值处理,不改变查询结构"""

    def test_keyword_injection_is_literal(self, server):
        base, _ = server
        payloads = [
            "' OR '1'='1",
            "'; DROP TABLE docs; --",
            "%' OR 1=1 --",
            "\" OR \"1\"=\"1",
            "x' UNION SELECT * FROM docs--",
        ]
        for p in payloads:
            r = get(base, "/api/search?q=" + urllib.parse.quote(p))
            assert "results" in r  # 正常响应,不报语法错误
            assert all(isinstance(x["path"], str) for x in r["results"])
        # 分类注入同样按字面值处理
        r = get(base, "/api/search?q=TOKEN_ONE&cat=" + urllib.parse.quote("' OR '1'='1"))
        assert "results" in r

    def test_db_still_intact_after_injection_attempts(self, server):
        base, _ = server
        get(base, "/api/search?q=" + urllib.parse.quote("'; DROP TABLE docs; --"))
        get(base, "/api/list?cat=" + urllib.parse.quote("' OR '1'='1; --"))
        # 库仍在且可正常查询
        r = get(base, "/api/search?q=TOKEN_ONE")
        assert r["results"][0]["path"] == "code/a.md"


def post(base, path, body=None, raw=False):
    data = body if raw else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data.encode("utf-8") if isinstance(data, str) else data, method="POST"
    )
    try:
        with _urlopen_retry(req) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


class TestWebAPI:
    def test_stats(self, server):
        base, _ = server
        s = get(base, "/api/stats")
        assert s["count"] == 1 and "code" in s["categories"]

    def test_search(self, server):
        base, _ = server
        r = get(base, "/api/search?q=TOKEN_ONE")
        assert r["results"][0]["path"] == "code/a.md"

    def test_search_with_cat_filter(self, server):
        """回归: search+cat 的 SQL 拼接曾把 AND cat 放在 LIMIT 后导致语法错误"""
        base, _ = server
        r = get(base, "/api/search?q=TOKEN_ONE&cat=code")
        assert len(r["results"]) == 1 and r["results"][0]["path"] == "code/a.md"
        r = get(base, "/api/search?q=TOKEN_ONE&cat=uploads")
        assert r["results"] == []

    def test_list_and_show(self, server):
        base, _ = server
        assert get(base, "/api/list")["docs"][0]["path"] == "code/a.md"
        d = get(base, "/api/show?path=code/a.md")
        assert d["title"] == "A" and "TOKEN_ONE" in d["body"]

    def test_upload_and_search(self, server):
        base, docs = server
        r = post(base, "/api/upload?filename=note.md", "# Note\n\nUPLOADED_MARKER", raw=True)
        assert r["ok"] and r["count"] == 2 and (docs / "uploads" / "note.md").exists()
        hits = get(base, "/api/search?q=UPLOADED_MARKER")
        assert hits["results"][0]["path"] == "uploads/note.md"

    def test_upload_dedupe(self, server):
        base, docs = server
        post(base, "/api/upload?filename=note.md", "one", raw=True)
        # 默认策略（error）: 同名冲突 → 409 提示,不写新文件,不覆盖原文件
        r = post(base, "/api/upload?filename=note.md", "two", raw=True)
        assert "error" in r and "文件已存在" in r["error"]
        assert not (docs / "uploads" / "note-1.md").exists()
        assert (docs / "uploads" / "note.md").read_text(encoding="utf-8") == "one"

    def test_upload_overwrite(self, server):
        base, docs = server
        post(base, "/api/upload?filename=note.md", "one", raw=True)
        r = post(base, "/api/upload?filename=note.md&if_exists=overwrite", "two", raw=True)
        assert r["ok"] and r["path"] == "uploads/note.md"  # 覆盖同一路径
        assert (docs / "uploads" / "note.md").read_text(encoding="utf-8") == "two"
        assert not (docs / "uploads" / "note-1.md").exists()

    def test_upload_keep_new_file(self, server):
        base, docs = server
        post(base, "/api/upload?filename=note.md", "one", raw=True)
        r = post(base, "/api/upload?filename=note.md&if_exists=keep", "two", raw=True)
        assert r["ok"] and r["path"] == "uploads/note-1.md"
        assert (docs / "uploads" / "note.md").read_text(encoding="utf-8") == "one"  # 原文件不动

    def test_upload_unknown_policy_rejected(self, server):
        base, _ = server
        r = post(base, "/api/upload?filename=n.md&if_exists=bogus", "x", raw=True)
        assert "error" in r and "未知 if_exists" in r["error"]

    def test_upload_rejects_non_md(self, server):
        base, _ = server
        r = post(base, "/api/upload?filename=x.exe", "data", raw=True)
        assert "error" in r and not r.get("ok")

    def test_upload_rejects_traversal(self, server):
        base, docs = server
        r = post(base, "/api/upload?filename=..%2Fevil.md", "data", raw=True)
        # 文件名消毒后落 uploads/，绝不逃出文档根
        assert r.get("ok") and not (docs.parent / "evil.md").exists()

    def test_delete_uploaded_only(self, server):
        base, docs = server
        post(base, "/api/upload?filename=temp.md", "data", raw=True)
        r = post(base, "/api/delete?path=uploads/temp.md")
        assert r["ok"] and not (docs / "uploads" / "temp.md").exists()
        # 库内文档禁止删除
        r = post(base, "/api/delete?path=code/a.md")
        assert "error" in r and (docs / "code" / "a.md").exists()

    def test_delete_traversal_blocked(self, server):
        base, _ = server
        r = post(base, "/api/delete?path=../../secrets.md")
        assert "error" in r
