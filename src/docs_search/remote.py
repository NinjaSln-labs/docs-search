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

认证: 远端服务可启用 Bearer token 或 Basic 认证——客户端用 token / username+password
构造 Authorization 头（与 web.py 的 AuthConfig 对应）。
代理: 连接层代理由 opener 决定——未配置跟随环境/系统代理（http_proxy/all_proxy/no_proxy，urllib 惯例）；
  proxy="direct"/"none"/"off" 强制直连（清空代理，环境代理拦内网/回环时用）；proxy=<URL> 固定走
  该 HTTP 代理（裸 host:port 补 scheme；socks 协议不支持——纯标准库限制，显式报错）。

错误语义: 业务错误（服务端 JSON error / 非 2xx）返回 dict，不抛异常；
连接失败/响应格式异常抛 RemoteError。安全防护（.md only、≤10MB、文件名消毒、
删除仅限 uploads/）由服务端强制执行，客户端原样转发、不绕过——只连可信服务。
"""

import base64
import json
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

HTTP_TIMEOUT = 10  # 秒；agent 调用不应挂死
HTTP_ATTEMPTS = 3  # 连接层短重试（Windows 安全软件偶发掐断回环连接,坑: WinError 10053）


class RemoteError(Exception):
    """远程服务错误（连接失败 / 响应格式异常）"""


PROXY_DIRECT_TOKENS = ("none", "direct", "off")  # --proxy 直连哨兵值(大小写不敏感)


class _ExplicitProxyHandler(urllib.request.ProxyHandler):
    """显式代理(--proxy URL)专用 handler——与 stdlib ProxyHandler 的唯一差异:
    跳过 proxy_bypass 旁路检查。显式配置是用户明确意图,不应被环境 no_proxy/
    系统代理排除规则(如 Windows 注册表 ProxyOverride 含 127.*)静默推翻;
    未配 --proxy 的默认路径仍走 stdlib 原生 handler,完整保留 bypass 语义。
    代理值已由 normalize_proxy 规范化为 http(s)://[user:pass@]host:port。
    """

    def __init__(self, proxy_url):
        super().__init__({})  # 不注册 stdlib scheme 映射,避免与覆盖方法叠加
        self._proxy_url = proxy_url

    def _proxy_open(self, req):
        # 语义对齐 stdlib ProxyHandler.proxy_open(除 bypass 外),仅用公开 API:
        # user:pass@ → Proxy-Authorization;set_proxy 记录 CONNECT 隧道目标后交回 chain
        parsed = urllib.parse.urlsplit(self._proxy_url)
        if parsed.username and parsed.password:
            raw = f"{urllib.parse.unquote(parsed.username)}:{urllib.parse.unquote(parsed.password)}"
            req.add_header("Proxy-authorization", "Basic " + base64.b64encode(raw.encode()).decode("ascii"))
        hostport = urllib.parse.unquote(parsed.netloc.rpartition("@")[2])
        req.set_proxy(hostport, req.type)
        # 落空返回 None(与 stdlib 同 scheme 代理分支一致) → 交给 HTTPHandler/HTTPSHandler(https 走 CONNECT)

    def http_open(self, req):
        return self._proxy_open(req)

    def https_open(self, req):
        return self._proxy_open(req)


def normalize_proxy(proxy):
    """清洗代理配置: 返回 (kind, value)——
      (None, None)          未配置 → 默认跟随环境/系统代理(urllib 惯例)
      ("direct", None)      强制直连 → 清空代理(忽略 http_proxy/all_proxy 等环境代理)
      ("url", "http://...") 显式 HTTP 代理(裸 host:port 自动补 http://)
    socks 系协议/非法地址抛 RemoteError(纯标准库 urllib 无 socks 实现)。
    scheme 判定用 '://' 前缀检测(同 normalize_url,避开 3.10 urlsplit 对裸串的猜测差异)。
    """
    val = (proxy or "").strip()
    if not val:
        return None, None
    if val.lower() in PROXY_DIRECT_TOKENS:
        return "direct", None
    if "://" not in val:
        val = "http://" + val
    parsed = urllib.parse.urlsplit(val)
    if parsed.scheme in ("socks", "socks4", "socks4a", "socks5", "socks5h"):
        raise RemoteError(
            f"代理协议不支持: {parsed.scheme}://(纯标准库 urllib 无 socks 实现;"
            "换用代理的 http 端口,或 direct 直连)")
    if parsed.scheme != "http" or not parsed.netloc:
        # https 代理(TLS 代理)不被 stdlib 连接序列支持(先明文 CONNECT 后 TLS,语义混乱)——显式报错;
        # https 目标经 http 代理由 stdlib CONNECT 隧道处理,不受影响
        raise RemoteError(f"非法代理地址: {proxy!r}(仅支持 http://proxy:port,或 direct 直连)")
    return "url", val


def normalize_url(url):
    """清洗服务地址: 补 scheme、去尾部斜杠；非法返回 None。
    补 scheme 用 '://' 判定（不依赖 urlsplit 对裸串的 scheme 猜测——
    Python 3.10 会把 '192.168.1.10:8765' 误判为 scheme，3.12+ 行为不同）"""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url  # 允许裸 ip:port / host:port 写法
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url.rstrip("/")


class RemoteClient:
    """连接已运行的 docs-search 服务；方法与 /api/* 端点一一对应，返回解析后的 JSON"""

    def __init__(self, base_url, token=None, username=None, password=None, proxy=None):
        self.base = normalize_url(base_url)
        if not self.base:
            raise RemoteError(f"非法服务地址: {base_url!r}（应为 http://ip:port 或 http://host:port）")
        if token:
            self._auth_header = f"Bearer {token}"
        elif username is not None or password is not None:
            if username is None or password is None:
                raise RemoteError("Basic 认证需要同时提供 username 与 password")
            raw = f"{username}:{password}".encode()
            self._auth_header = "Basic " + base64.b64encode(raw).decode("ascii")
        else:
            self._auth_header = None
        # 连接层代理: 每客户端独立 opener(不用全局 urlopen 的缓存 opener,环境读取时机确定,可测)
        kind, value = normalize_proxy(proxy)
        if kind == "direct":
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif kind == "url":
            self._opener = urllib.request.build_opener(_ExplicitProxyHandler(value))
        else:
            self._opener = urllib.request.build_opener()  # 默认: 与 urlopen 同源,跟随环境/系统代理

    def _request(self, method, path, query=None, body=None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "text/plain; charset=utf-8")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        last = None
        for attempt in range(HTTP_ATTEMPTS):
            try:
                with self._opener.open(req, timeout=HTTP_TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except HTTPError as e:
                # 业务错误: 服务端以 JSON {"error": ...} 响应（400/401/403/404/413/500...）
                try:
                    data = json.loads(e.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    data = None
                if e.code == 401:
                    return {"error": "unauthorized（认证失败——远端启用了认证，检查 --token 或 --user/--password）"}
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

    def stats(self, workspace=None):
        return self._request("GET", "/api/stats", {"ws": workspace})

    def search(self, q, cat=None, workspace=None):
        query = {"q": q, "ws": workspace}
        if cat:
            query["cat"] = cat
        return self._request("GET", "/api/search", query)

    def list(self, cat=None, workspace=None):
        query = {}
        if cat:
            query["cat"] = cat
        if workspace:
            query["ws"] = workspace
        return self._request("GET", "/api/list", query)

    def show(self, path, workspace=None):
        return self._request("GET", "/api/show", {"path": path, "ws": workspace})

    def upload(self, filename, content, if_exists="error", workspace=None):
        query = {"filename": filename, "if_exists": if_exists, "ws": workspace}
        return self._request("POST", "/api/upload", query, content.encode("utf-8"))

    def delete(self, path, workspace=None):
        return self._request("POST", "/api/delete", {"path": path, "ws": workspace})
