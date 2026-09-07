"""docs_search.remote: 远程 docs-search 服务客户端（纯标准库 urllib）

MCP server 远程模式使用: 用户已运行 docs-search-web（或任意接口相同的服务）时，
不读本地目录、不起本地索引，改走 HTTP 调用该服务——5 个 MCP 工具与 /api/* 一一对应。

接口契约（= web.py 的 REST API）:
  GET  /api/stats            -> {"count", "updated", "categories"}
  GET  /api/search?q=&cat=   -> {"results": [{path, cat, title, snippet}]}
  GET  /api/list?cat=        -> {"docs": [{path, title, size}]}
  GET  /api/show?path=       -> {title, body, size, cat, path} | {"error": ...}
  POST /api/upload?filename= (raw body) -> {"ok", "path", "count"} | {"error": ...}
  POST /api/delete?path=     -> {"ok", "deleted", "count"} | {"error": ...}

错误语义: 业务错误（服务端 JSON error / 非 2xx）返回 dict，不抛异常；
连接失败/响应格式异常抛 RemoteError。安全防护（.md only、≤10MB、文件名消毒、
删除仅限 uploads/）由服务端强制执行，客户端原样转发、不绕过——只连可信服务。
"""

import json
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

HTTP_TIMEOUT = 10  # 秒；agent 调用不应挂死
HTTP_ATTEMPTS = 3  # 连接层短重试（Windows 安全软件偶发掐断回环连接,坑: WinError 10053）


class RemoteError(Exception):
    """远程服务错误（连接失败 / 响应格式异常）"""


def normalize_url(url):
    """清洗服务地址: 补 scheme、去尾部斜杠；非法返回 None"""
    url = (url or "").strip()
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None  # 显式非 http(s) scheme → 拒绝
    if not parsed.scheme:
        url = "http://" + url  # 允许裸 ip:port / host:port 写法
        parsed = urllib.parse.urlsplit(url)
    if not parsed.netloc:
        return None
    return url.rstrip("/")


class RemoteClient:
    """连接已运行的 docs-search 服务；方法与 /api/* 端点一一对应，返回解析后的 JSON"""

    def __init__(self, base_url):
        self.base = normalize_url(base_url)
        if not self.base:
            raise RemoteError(f"非法服务地址: {base_url!r}（应为 http://ip:port 或 http://host:port）")

    def _request(self, method, path, query=None, body=None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "text/plain; charset=utf-8")
        last = None
        for attempt in range(HTTP_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except HTTPError as e:
                # 业务错误: 服务端以 JSON {"error": ...} 响应（400/403/404/413/500...）
                try:
                    data = json.loads(e.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict) and "error" in data:
                    return data
                return {"error": f"服务端错误(HTTP {e.code})"}
            except (URLError, OSError) as e:
                # 连接层错误(拒绝/超时/回环被掐)短退避重试,最后一次向上抛
                last = e
                time.sleep(0.05 * (attempt + 1))
        else:
            raise RemoteError(f"无法连接服务 {self.base}: {last}") from last
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RemoteError(f"服务响应不是 JSON（接口不符合 docs-search 契约？）: {raw[:120]!r}") from e
        if not isinstance(data, dict):
            raise RemoteError(f"服务响应格式异常（应为 JSON 对象）: {raw[:120]!r}")
        return data

    def stats(self):
        return self._request("GET", "/api/stats")

    def search(self, q, cat=None):
        query = {"q": q}
        if cat:
            query["cat"] = cat
        return self._request("GET", "/api/search", query)

    def list(self, cat=None):
        query = {} if not cat else {"cat": cat}
        return self._request("GET", "/api/list", query)

    def show(self, path):
        return self._request("GET", "/api/show", {"path": path})

    def upload(self, filename, content):
        return self._request("POST", "/api/upload", {"filename": filename}, content.encode("utf-8"))

    def delete(self, path):
        return self._request("POST", "/api/delete", {"path": path})
