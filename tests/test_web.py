"""Web API 测试（内存 HTTP 服务 + urllib）"""

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from docs_search.web import make_handler


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


def get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return json.loads(r.read().decode("utf-8"))


def post(base, path, body=None, raw=False):
    data = body if raw else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data.encode("utf-8") if isinstance(data, str) else data, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
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
        base, _ = server
        post(base, "/api/upload?filename=note.md", "one", raw=True)
        r = post(base, "/api/upload?filename=note.md", "two", raw=True)
        assert r["path"] == "uploads/note-1.md"

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
