"""远程服务客户端（RemoteClient）单元测试——契约 = docs-search-web 的 /api/*

用内存 HTTP 服务（web.make_handler，与生产同源）模拟「用户已运行的服务」，
验证 5 个 MCP 工具的远程后端正确代理、业务错误与连接失败语义。
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from docs_search.remote import RemoteClient, RemoteError, normalize_url
from docs_search.web import make_handler


@pytest.fixture
def server(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hello.md").write_text("# Hello\n\nunique token FROBNICATOR\n", encoding="utf-8")
    (docs / "infra").mkdir()
    (docs / "infra" / "mcp.md").write_text("# MCP\n\n中文检索令牌甲乙丙\n", encoding="utf-8")
    db = tmp_path / "idx.db"
    srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", docs
    srv.shutdown()


class TestNormalizeUrl:
    def test_scheme_kept(self):
        assert normalize_url("http://192.168.1.10:8765") == "http://192.168.1.10:8765"

    def test_bare_host_port_gets_scheme(self):
        assert normalize_url("192.168.1.10:8765") == "http://192.168.1.10:8765"

    def test_trailing_slash_and_space_stripped(self):
        assert normalize_url(" http://host:8765/ ") == "http://host:8765"

    @pytest.mark.parametrize("bad", ["", None, "ftp://x:1", "  "])
    def test_invalid(self, bad):
        assert normalize_url(bad) is None

    def test_client_rejects_invalid(self):
        with pytest.raises(RemoteError):
            RemoteClient("ftp://x:1")


class TestRemoteClient:
    def test_stats_and_search(self, server):
        base, _ = server
        c = RemoteClient(base)
        s = c.stats()
        assert s["count"] == 2 and "infra" in s["categories"]

        r = c.search("FROBNICATOR")
        assert r["results"][0]["path"] == "hello.md"
        assert "snippet" in r["results"][0]

        # CJK + cat 过滤（web.py search+cat SQL 拼接回归）
        r = c.search("甲乙丙", cat="infra")
        assert r["results"][0]["path"] == "infra/mcp.md"
        r = c.search("FROBNICATOR", cat="infra")
        assert r["results"] == []

    def test_list_and_show(self, server):
        base, _ = server
        c = RemoteClient(base)
        assert {d["path"] for d in c.list()["docs"]} == {"hello.md", "infra/mcp.md"}
        assert {d["path"] for d in c.list(cat="infra")["docs"]} == {"infra/mcp.md"}

        d = c.show("hello.md")
        assert d["title"] == "Hello" and "FROBNICATOR" in d["body"] and d["cat"] == "hello.md"
        assert "error" in c.show("ghost.md")

    def test_upload_delete_roundtrip_with_guards(self, server):
        base, docs = server
        c = RemoteClient(base)
        r = c.upload("note.md", "# Note\n\nUPLOADED_MARKER\n")
        assert r["ok"] and r["path"] == "uploads/note.md" and r["count"] == 3
        assert (docs / "uploads" / "note.md").exists()

        # 同名 → 服务端去重 -1
        r = c.upload("note.md", "two\n")
        assert r["path"] == "uploads/note-1.md"

        # 非 .md → 服务端拒绝（400 + JSON error）
        assert "error" in c.upload("x.exe", "data")

        # 删除守卫: 库内文档 / 路径穿越 → error dict;uploads 内 → 成功
        assert "error" in c.delete("hello.md")
        assert "error" in c.delete("../../secrets.md")
        r = c.delete("uploads/note.md")
        assert r["ok"] and not (docs / "uploads" / "note.md").exists()

    def test_connection_refused_raises(self):
        c = RemoteClient("http://127.0.0.1:1")  # 端口 1 无服务
        with pytest.raises(RemoteError, match="无法连接服务"):
            c.stats()


def test_non_docs_search_interface_rejected(tmp_path):
    """接口不一致的服务(非 JSON 响应)→ RemoteError,而非静默吞掉"""

    class OddHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"this is not json")

        def log_message(self, fmt, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), OddHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = RemoteClient(f"http://127.0.0.1:{port}")
        with pytest.raises(RemoteError, match="不是 JSON"):
            c.stats()
    finally:
        srv.shutdown()
