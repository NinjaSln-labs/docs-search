"""MCP server 端到端测试(子进程直跑 scripts/docs-search-mcp.py,JSON-RPC 2.0 over stdio)

覆盖: 握手 / 工具清单 / 检索(含 CJK,坑:Windows GBK)/ 读写删 / 守卫 / 未知方法 / 噪声行 / EOF 退出
以及远程模式(--url 连已运行服务,后端不读本地目录)。
"""

import json
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from docs_search.web import AuthConfig, make_handler

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "docs-search-mcp.py"


class McpClient:
    """MCP stdio 测试客户端——按行写 JSON-RPC,按行读响应;支持本地(--dir)与远程(--url/--token)"""

    def __init__(self, docs_dir=None, url=None, token=None):
        if url:
            args = ["--url", url]
            if token:
                args += ["--token", token]
        else:
            args = [str(docs_dir)]
        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=REPO,
            env={
                "PYTHONIOENCODING": "utf-8",
                "PATH": "",
                "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                "USERPROFILE": __import__("os").environ.get("USERPROFILE", ""),
                "HOME": __import__("os").environ.get("HOME", ""),
            },
        )
        self._next_id = 0

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def send_raw(self, text):
        self.proc.stdin.write((text + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def recv(self):
        line = self.proc.stdout.readline().decode("utf-8")
        assert line, "server closed stdout unexpectedly"
        return json.loads(line)

    def request(self, method, params=None):
        self._next_id += 1
        req = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            req["params"] = params
        self.send(req)
        return self.recv()

    def call(self, tool, arguments):
        return self.request("tools/call", {"name": tool, "arguments": arguments})

    def text(self, resp):
        assert "error" not in resp, resp
        return resp["result"]["content"][0]["text"]

    def initialize(self):
        resp = self.request("initialize", {"protocolVersion": "2024-11-05"})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def close(self):
        self.proc.stdin.close()
        return self.proc.wait(timeout=10)


@pytest.fixture
def web_server(tmp_path):
    """已运行的 docs-search 服务(内存 HTTP,契约同 docs-search-web)——远程模式后端"""
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


@pytest.fixture
def web_server_auth(tmp_path):
    """已运行且启用 Bearer 认证的 docs-search 服务——远程模式 + 认证场景"""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hello.md").write_text("# Hello\n\nunique token FROBNICATOR\n", encoding="utf-8")
    db = tmp_path / "idx.db"
    srv = HTTPServer(("127.0.0.1", 0), make_handler(docs, db, AuthConfig(token="tk-e2e")))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", docs
    srv.shutdown()


def make_corpus(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hello.md").write_text("# Hello\n\nunique token FROBNICATOR\n", encoding="utf-8")
    sub = docs / "infra"
    sub.mkdir()
    (sub / "mcp.md").write_text("# MCP\n\n中文检索令牌甲乙丙\n", encoding="utf-8")
    return docs


def test_initialize_and_tools_list(tmp_path):
    docs = make_corpus(tmp_path)
    c = McpClient(docs)
    try:
        resp = c.initialize()
        assert resp["result"]["serverInfo"]["name"] == "docs-search"
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in resp["result"]["capabilities"]

        resp = c.request("tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["docs_search", "docs_read", "docs_write", "docs_delete", "docs_info"]
        for t in resp["result"]["tools"]:
            assert t["inputSchema"]["type"] == "object"
            assert t["description"]
    finally:
        assert c.close() == 0  # stdin EOF → 干净退出


def test_search_and_read_with_cjk(tmp_path):
    docs = make_corpus(tmp_path)
    c = McpClient(docs)
    try:
        c.initialize()
        out = c.text(c.call("docs_search", {"query": "FROBNICATOR"}))
        assert "hello.md" in out and "1 results" in out

        # CJK 关键词——验证 stdio 显式 UTF-8(Windows GBK 管道坑)
        out = c.text(c.call("docs_search", {"query": "甲乙丙"}))
        assert "infra/mcp.md" in out

        # 多关键词 AND
        out = c.text(c.call("docs_search", {"query": "unique FROBNICATOR"}))
        assert "hello.md" in out
        out = c.text(c.call("docs_search", {"query": "unique 甲乙丙"}))
        assert "no results" in out

        # 空关键词 → isError
        resp = c.call("docs_search", {"query": "  "})
        assert resp["result"].get("isError") is True

        # 全文读取 + 未命中
        assert "FROBNICATOR" in c.text(c.call("docs_read", {"path": "hello.md"}))
        resp = c.call("docs_read", {"path": "ghost.md"})
        assert resp["result"].get("isError") is True and "文档不存在" in resp["result"]["content"][0]["text"]
    finally:
        c.close()


def test_write_delete_roundtrip_with_guards(tmp_path):
    docs = make_corpus(tmp_path)
    c = McpClient(docs)
    try:
        c.initialize()
        # 写入 → 立即可搜
        out = c.text(c.call("docs_write", {"filename": "session.md", "content": "# 结论\n\nTOKEN_XYZ_987\n"}))
        assert "uploads/session.md" in out
        assert "TOKEN_XYZ_987" in c.text(c.call("docs_search", {"query": "TOKEN_XYZ_987"}))

        # 同名写入(默认 if_exists=error) → isError 提示,不覆盖不新增
        resp = c.call("docs_write", {"filename": "session.md", "content": "second\n"})
        assert resp["result"].get("isError") is True and "文件已存在" in resp["result"]["content"][0]["text"]
        # if_exists=keep → 自动去重 -1;if_exists=overwrite → 覆盖原文件
        out = c.text(c.call("docs_write", {"filename": "session.md", "content": "second\n", "if_exists": "keep"}))
        assert "uploads/session-1.md" in out
        out = c.text(c.call("docs_write", {"filename": "session.md", "content": "v2\n", "if_exists": "overwrite"}))
        assert "uploads/session.md" in out
        assert "v2" in c.text(c.call("docs_read", {"path": "uploads/session.md"}))
        # 非法 if_exists → isError
        resp = c.call("docs_write", {"filename": "x.md", "content": "x", "if_exists": "bogus"})
        assert resp["result"].get("isError") is True

        # 文件名消毒: 路径穿越降级为 basename
        out = c.text(c.call("docs_write", {"filename": "a/b/../../evil.md", "content": "evil\n"}))
        assert "uploads/evil.md" in out

        # 非 .md → isError
        resp = c.call("docs_write", {"filename": "note.txt", "content": "x"})
        assert resp["result"].get("isError") is True

        # 删除守卫: 路径穿越 / 库内文档 → isError;uploads 内 → 成功
        resp = c.call("docs_delete", {"path": "../outside.md"})
        assert "仅允许删除 uploads/" in c.text(resp)
        resp = c.call("docs_delete", {"path": "hello.md"})
        assert "仅允许删除 uploads/" in c.text(resp)
        out = c.text(c.call("docs_delete", {"path": "uploads/evil.md"}))
        assert "deleted" in out

        # stats 模式
        out = c.text(c.call("docs_info", {"mode": "stats"}))
        assert '"count": 4' in out  # hello + infra/mcp + session + session-1
    finally:
        c.close()


def test_info_list_and_protocol_robustness(tmp_path):
    docs = make_corpus(tmp_path)
    c = McpClient(docs)
    try:
        c.initialize()
        # list 模式
        out = c.text(c.call("docs_info", {}))
        assert "hello.md" in out and "infra/mcp.md" in out
        out = c.text(c.call("docs_info", {"cat": "infra"}))
        assert "mcp.md" in out and "hello.md" not in out

        # ping
        assert c.request("ping")["result"] == {}

        # 未知方法(带 id)→ -32601;未知工具 → -32602
        resp = c.request("resources/list")
        assert resp["error"]["code"] == -32601
        resp = c.call("no_such_tool", {})
        assert resp["error"]["code"] == -32602

        # 通知(initialized 重复发)无响应且不炸;噪声行被忽略;之后协议继续可用
        c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        c.send_raw("this is not json")
        c.send_raw("[]")
        resp = c.request("ping")
        assert resp["result"] == {}
    finally:
        c.close()


def test_missing_docs_dir_creates_empty_library(tmp_path):
    missing = tmp_path / "not-yet" / "docs"
    c = McpClient(missing)
    try:
        c.initialize()
        out = c.text(c.call("docs_info", {}))
        assert "empty" in out
    finally:
        c.close()


def test_remote_mode_full_roundtrip(tmp_path, web_server):
    """远程模式: 不读本地目录,5 工具全部代理到已运行服务;输出格式与本地模式一致"""
    base, _ = web_server
    c = McpClient(url=base)
    try:
        c.initialize()
        # 工具清单与本地模式一致
        resp = c.request("tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == ["docs_search", "docs_read", "docs_write", "docs_delete", "docs_info"]

        # 检索: 英文 + CJK,cat 过滤走 HTTP(web.py search+cat SQL 回归)
        out = c.text(c.call("docs_search", {"query": "FROBNICATOR"}))
        assert "hello.md" in out and "1 results" in out
        out = c.text(c.call("docs_search", {"query": "甲乙丙"}))
        assert "infra/mcp.md" in out
        out = c.text(c.call("docs_search", {"query": "FROBNICATOR", "cat": "infra"}))
        assert "no results" in out

        # 全文读取
        assert "FROBNICATOR" in c.text(c.call("docs_read", {"path": "hello.md"}))
        resp = c.call("docs_read", {"path": "ghost.md"})
        assert resp["result"].get("isError") is True and "文档不存在" in resp["result"]["content"][0]["text"]

        # 写入 → 立即可搜
        out = c.text(c.call("docs_write", {"filename": "remote-session.md", "content": "# R\n\nREMOTE_TOKEN_42\n"}))
        assert "uploads/remote-session.md" in out
        assert "REMOTE_TOKEN_42" in c.text(c.call("docs_search", {"query": "REMOTE_TOKEN_42"}))

        # 删除: uploads 内成功,库内文档被服务端守卫拒绝
        out = c.text(c.call("docs_delete", {"path": "uploads/remote-session.md"}))
        assert "deleted" in out
        resp = c.call("docs_delete", {"path": "hello.md"})
        assert resp["result"].get("isError") is True and "仅允许删除 uploads/" in resp["result"]["content"][0]["text"]

        # info: stats + list(与本地模式同输出格式)
        out = c.text(c.call("docs_info", {"mode": "stats"}))
        assert '"count": 2' in out
        out = c.text(c.call("docs_info", {}))
        assert "hello.md" in out and "infra/mcp.md" in out
        out = c.text(c.call("docs_info", {"cat": "infra"}))
        assert "mcp.md" in out and "hello.md" not in out
    finally:
        c.close()


def test_remote_mode_unreachable_service(tmp_path):
    """服务不可达 → 启动探活失败,进程立即退出非零(而非挂起无输出)"""
    c = McpClient(url="http://127.0.0.1:1")
    rc = c.proc.wait(timeout=20)
    err = c.proc.stderr.read().decode("utf-8")
    assert rc != 0
    assert "无法连接" in err


def test_remote_mode_with_auth_roundtrip(tmp_path, web_server_auth):
    """远程模式 + Bearer 认证: 正确凭据可用,错误凭据探活直接失败退出"""
    base, _ = web_server_auth
    # 正确凭据 → 5 工具可用（抽样验证检索）
    c = McpClient(url=base, token="tk-e2e")
    try:
        c.initialize()
        out = c.text(c.call("docs_search", {"query": "FROBNICATOR"}))
        assert "hello.md" in out and "1 results" in out
    finally:
        c.close()

    # 错误凭据 → 启动探活失败,退出非零、报错提示
    bad = McpClient(url=base, token="wrong-token")
    rc = bad.proc.wait(timeout=20)
    err = bad.proc.stderr.read().decode("utf-8")
    assert rc != 0
    assert "认证失败" in err
