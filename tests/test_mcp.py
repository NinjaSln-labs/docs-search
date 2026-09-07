"""MCP server 端到端测试(子进程直跑 scripts/docs-search-mcp.py,JSON-RPC 2.0 over stdio)

覆盖: 握手 / 工具清单 / 检索(含 CJK,坑:Windows GBK)/ 读写删 / 守卫 / 未知方法 / 噪声行 / EOF 退出
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "docs-search-mcp.py"


class McpClient:
    """MCP stdio 测试客户端——按行写 JSON-RPC,按行读响应"""

    def __init__(self, docs_dir):
        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), str(docs_dir)],
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

        # 同名写入 → 自动去重 -1
        out = c.text(c.call("docs_write", {"filename": "session.md", "content": "second\n"}))
        assert "uploads/session-1.md" in out

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
