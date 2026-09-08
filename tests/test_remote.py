"""远程服务客户端（RemoteClient）单元测试——契约 = docs-search-web 的 /api/*

用内存 HTTP 服务（web.make_handler，与生产同源）模拟「用户已运行的服务」，
验证 5 个 MCP 工具的远程后端正确代理、业务错误与连接失败语义；
连接层代理（normalize_proxy / opener）用内存正向代理验证真实路由。
"""

import base64
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from docs_search.remote import PROXY_DIRECT_TOKENS, RemoteClient, RemoteError, normalize_proxy, normalize_url
from docs_search.web import AuthConfig, make_handler


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

        # 同名默认(error) → 冲突错误 dict,原文件不动
        r = c.upload("note.md", "two\n")
        assert "error" in r and "文件已存在" in r["error"]
        # if_exists=keep → 服务端去重 -1;overwrite → 覆盖
        r = c.upload("note.md", "two\n", "keep")
        assert r["path"] == "uploads/note-1.md"
        r = c.upload("note.md", "three\n", "overwrite")
        assert r["path"] == "uploads/note.md" and (docs / "uploads" / "note.md").read_text(encoding="utf-8") == "three\n"

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


class TestRemoteAuth:
    """远端服务启用认证时,客户端必须携带正确凭据"""

    def _auth_server(self, tmp_path, auth):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("# A\n\nSECRET_MARKER\n", encoding="utf-8")
        db = tmp_path / "idx.db"
        srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db, auth))
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return f"http://127.0.0.1:{port}", srv

    def test_bearer_roundtrip(self, tmp_path):
        base, srv = self._auth_server(tmp_path, AuthConfig(token="tk-123"))
        try:
            good = RemoteClient(base, token="tk-123")
            assert good.stats()["count"] == 1
            assert good.search("SECRET_MARKER")["results"][0]["path"] == "a.md"

            # 无凭据 / 错凭据 → 明确 401 业务错误
            bad = RemoteClient(base)
            assert bad.stats() == {"error": "unauthorized（认证失败——远端启用了认证，检查 --token 或 --user/--password）"}
            wrong = RemoteClient(base, token="wrong")
            assert "unauthorized" in wrong.stats()["error"]
        finally:
            srv.shutdown()

    def test_basic_roundtrip(self, tmp_path):
        base, srv = self._auth_server(tmp_path, AuthConfig(username="admin", password="pw"))
        try:
            c = RemoteClient(base, username="admin", password="pw")
            assert c.stats()["count"] == 1
            assert "unauthorized" in RemoteClient(base).stats()["error"]
            assert "unauthorized" in RemoteClient(base, username="admin", password="badx").stats()["error"]
        finally:
            srv.shutdown()

    def test_partial_basic_credentials_rejected(self):
        with pytest.raises(RemoteError, match="username 与 password"):
            RemoteClient("http://127.0.0.1:1", username="u")

    def test_workspace_passthrough(self, tmp_path, monkeypatch):
        """workspace 经 ws= query 透传到服务端（远程模式下库由服务端切换）"""
        fake_home = tmp_path / "fakehome"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        base, srv = self._auth_server(tmp_path, AuthConfig())
        try:
            c = RemoteClient(base)
            # 上传到默认库 + workspace 库
            assert c.upload("f.md", "default-marker\n")["ok"]
            assert c.upload("f.md", "ws-marker\n", workspace="wx-1")["ok"]
            # search: 默认找不到 ws,ws 能找到
            assert c.search("ws-marker")["results"] == []
            assert c.search("ws-marker", workspace="wx-1")["results"][0]["path"] == "uploads/f.md"
            # stats / list 区分
            assert c.stats()["count"] == 2  # a.md + f.md
            assert c.stats(workspace="wx-1")["count"] == 1
            assert c.list(workspace="wx-1")["docs"][0]["path"] == "uploads/f.md"
        finally:
            srv.shutdown()

    def test_workspace_all_passthrough(self, tmp_path, monkeypatch):
        """workspace=all 经 ws=all 透传,服务端跨库聚合返回带 ws 来源"""
        fake_home = tmp_path / "fakehome"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        base, srv = self._auth_server(tmp_path, AuthConfig())
        try:
            c = RemoteClient(base)
            assert c.upload("d.md", "shared-marker-42\n")["ok"]
            assert c.upload("w.md", "shared-marker-42\n", workspace="wa")["ok"]
            r = c.search("shared-marker-42", workspace="all")
            assert r["workspace"] == "all"
            by_ws = {x["ws"]: x["path"] for x in r["results"]}
            assert by_ws == {"": "uploads/d.md", "wa": "uploads/w.md"}
            assert c.list(workspace="all")["workspace"] == "all"
        finally:
            srv.shutdown()


class TestProxyRouting:
    """连接层代理: 显式代理真实路由 / direct 清空环境代理 / socks 拒绝

    测试用内存正向代理（绝对 URI 请求行转发,urllib 走 http 代理的标准形状）。
    """

    @pytest.fixture
    def proxy_env_clean(self, monkeypatch):
        """清空代理相关环境变量,保证用例不受开发机全局代理影响"""
        for var in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            monkeypatch.delenv(var, raising=False)

    @pytest.fixture
    def forward_proxy(self):
        """内存正向代理: 记录请求行与头,直连转发目标,原样回传(支持 GET/POST)"""
        seen = []
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        class RelayHandler(BaseHTTPRequestHandler):
            def _relay(self):
                seen.append({
                    "line": self.requestline,
                    "auth": self.headers.get("Authorization"),
                    "proxy_auth": self.headers.get("Proxy-Authorization"),
                })
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                req = urllib.request.Request(self.path, data=body, method=self.command)
                for h in ("Authorization", "Content-Type"):
                    if self.headers.get(h):
                        req.add_header(h, self.headers[h])
                try:
                    with direct.open(req, timeout=10) as r:
                        payload, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
                except urllib.error.HTTPError as e:
                    payload, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._relay()

            def do_POST(self):
                self._relay()

            def log_message(self, fmt, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), RelayHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
        srv.shutdown()

    def test_explicit_proxy_routes_requests(self, server, forward_proxy, proxy_env_clean):
        """--proxy URL: 请求经代理(绝对 URI + 端到端凭据透传)到达服务并成功"""
        base, _ = server
        proxy_url, seen = forward_proxy
        c = RemoteClient(base, token="tk-x", proxy=proxy_url)
        assert c.stats()["count"] == 2
        assert c.search("FROBNICATOR")["results"][0]["path"] == "hello.md"
        assert c.upload("via-proxy.md", "PROXY_MARKER\n")["ok"]
        assert len(seen) >= 3
        assert all(s["line"].startswith(f"GET {base}/api/") or s["line"].startswith(f"POST {base}/api/") for s in seen)
        assert seen[0]["auth"] == "Bearer tk-x"  # 端到端凭据经代理原样透传

    def test_proxy_with_credentials(self, server, forward_proxy, proxy_env_clean):
        """代理地址含 user:pass → urllib 自动带 Proxy-Authorization"""
        base, _ = server
        proxy_url, seen = forward_proxy
        quoted = proxy_url.replace("http://", "http://u:p@")
        c = RemoteClient(base, proxy=quoted)
        assert c.stats()["count"] == 2
        expected = "Basic " + base64.b64encode(b"u:p").decode("ascii")
        assert seen[0]["proxy_auth"] == expected

    def test_direct_token_ignores_env_proxies(self, server, monkeypatch, proxy_env_clean):
        """direct/none/off 哨兵: 环境代理指向死端口也不影响直连"""
        base, _ = server
        dead = "http://127.0.0.1:1"
        for token in PROXY_DIRECT_TOKENS:
            monkeypatch.setenv("http_proxy", dead)
            monkeypatch.setenv("https_proxy", dead)
            monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1")
            c = RemoteClient(base, proxy=token)
            assert c.stats()["count"] == 2, f"proxy={token!r} 应直连成功"

    def test_socks_proxy_rejected(self, server):
        """socks 协议显式报错(纯标准库无实现),而非连接时晦涩失败"""
        base, _ = server
        with pytest.raises(RemoteError, match="socks"):
            RemoteClient(base, proxy="socks5://127.0.0.1:1080")
        with pytest.raises(RemoteError, match="socks"):
            RemoteClient(base, proxy="socks5h://127.0.0.1:1080")

    def test_invalid_proxy_rejected(self, server):
        base, _ = server
        with pytest.raises(RemoteError, match="非法代理地址"):
            RemoteClient(base, proxy="ftp://127.0.0.1:21")
        with pytest.raises(RemoteError, match="非法代理地址"):
            RemoteClient(base, proxy="http://")
        # https 代理(TLS 代理)不被 stdlib 连接序列支持——显式报错而非连接时晦涩失败
        with pytest.raises(RemoteError, match="非法代理地址"):
            RemoteClient(base, proxy="https://127.0.0.1:8443")

    def test_default_env_behavior(self, server, forward_proxy, proxy_env_clean, monkeypatch):
        """未配 --proxy 时默认跟随环境代理(http_proxy 指向内存代理 → 请求经代理)"""
        base, _ = server
        proxy_url, seen = forward_proxy
        monkeypatch.setenv("http_proxy", proxy_url)
        c = RemoteClient(base)
        assert c.stats()["count"] == 2
        assert len(seen) >= 1


class TestNormalizeProxy:
    def test_none_unset(self):
        assert normalize_proxy(None) == (None, None)
        assert normalize_proxy("") == (None, None)
        assert normalize_proxy("  ") == (None, None)

    def test_direct_tokens(self):
        for token in ("direct", "none", "off", "DIRECT"):
            assert normalize_proxy(token) == ("direct", None)

    def test_url_forms(self):
        assert normalize_proxy("http://127.0.0.1:7890") == ("url", "http://127.0.0.1:7890")
        assert normalize_proxy("127.0.0.1:7890") == ("url", "http://127.0.0.1:7890")
        assert normalize_proxy(" http://proxy.corp:8080 ") == ("url", "http://proxy.corp:8080")

    def test_socks_raises(self):
        for bad in ("socks5://h:1", "socks5h://h:1", "socks4://h:1", "SOCKS5://h:1"):
            with pytest.raises(RemoteError, match="socks"):
                normalize_proxy(bad)

    def test_invalid_raises(self):
        for bad in ("ftp://h:1", "http://", "https://h:1"):
            with pytest.raises(RemoteError, match="非法代理地址"):
                normalize_proxy(bad)
