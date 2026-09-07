"""docs-search-web: 文档搜索 Web UI（零依赖，含上传接口）

用法:
  python scripts/docs-search-web.py [文档目录]              # 启动并打开浏览器
  python scripts/docs-search-web.py --no-browser           # 不打开浏览器
  python scripts/docs-search-web.py --port 8080 --host 0.0.0.0
  python scripts/docs-search-web.py --token <TOKEN>        # 启用 Bearer 认证（远端部署用）
  python scripts/docs-search-web.py --user U --password P  # 启用 Basic 认证

路径规则（不绑定任何本地路径）:
  文档目录: 命令行 [目录] > 环境变量 DOCS_SEARCH_DIR > ./docs
  索引库:   ~/.docs-search/<目录哈希>/index.db（按目录隔离）

API:
  GET  /api/stats                    # 统计 {count, updated, categories}
  GET  /api/search?q=关键词&cat=     # 搜索
  GET  /api/list?cat=                # 列出文档
  GET  /api/show?path=xxx            # 文档内容
  POST /api/upload?filename=x.md&if_exists=error|overwrite|keep  # 上传文档（同名策略，默认 error 提示）
  POST /api/delete?path=uploads/x.md # 删除 uploads/ 下已上传文档

工作空间（可选）: --workspace <name> 或 $DOCS_SEARCH_WORKSPACE；索引库路径加
~/.docs-search/<workspace>/ 命名空间层，不填 = 默认库（路径与旧版一致）。

认证（远端部署）：--token（Bearer）/ --user+--password（Basic），二选一；
凭据也可用环境变量 DOCS_SEARCH_TOKEN / DOCS_SEARCH_USER / DOCS_SEARCH_PASSWORD。
所有路径（含 Web 页面）均受保护；未带/错带凭据返回 401。

安全: 默认仅监听 127.0.0.1；上传仅限 .md、单文件 ≤10MB、文件名已消毒；
所有 SQL 均为参数化查询（无注入面）。监听非回环地址时**必须**启用认证，否则拒绝启动。
"""

import argparse
import base64
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from .core import (
    ENV_WORKSPACE,
    IF_EXISTS_CHOICES,
    MAX_UPLOAD_BYTES,
    ensure_index,
    get_conn,
    load_meta,
    resolve_db_path,
    resolve_docs_dir,
    resolve_upload_target,
    resolve_workspace,
    sanitize_filename,
    win_utf8,
)

DEFAULT_PORT = 8765

ENV_TOKEN = "DOCS_SEARCH_TOKEN"
ENV_USER = "DOCS_SEARCH_USER"
ENV_PASSWORD = "DOCS_SEARCH_PASSWORD"


def validate_listen_config(host, auth):
    """监听非回环地址时必须启用认证；返回错误消息或 None（安全红线，测试覆盖）"""
    if host in ("", "127.0.0.1", "localhost", "::1") or auth.enabled:
        return None
    return "监听非回环地址时必须启用认证（--token 或 --user/--password），防止服务裸奔暴露"


class AuthConfig:
    """服务端认证配置: Bearer token 或 Basic 用户名/密码，二选一；均未配置则开放（仅限回环监听）"""

    def __init__(self, token=None, username=None, password=None):
        self.token = token
        self.username = username
        self.password = password

    @property
    def enabled(self):
        return bool(self.token or (self.username and self.password))

    def check(self, auth_header):
        """校验 Authorization 头；未启用认证恒通过"""
        if not self.enabled:
            return True
        if not auth_header:
            return False
        scheme, _, rest = auth_header.partition(" ")
        rest = rest.strip()
        if scheme.lower() == "bearer" and self.token:
            return hmac.compare_digest(rest, self.token)
        if scheme.lower() == "basic" and self.username:
            try:
                user, _, pw = base64.b64decode(rest).decode("utf-8").partition(":")
            except Exception:  # noqa: BLE001 -- 坏 Base64 直接拒绝
                return False
            return hmac.compare_digest(user, self.username) and hmac.compare_digest(pw, self.password)
        return False

# ============================================================
# HTML 界面
# ============================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>docs-search</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f5; color: #333; }
.container { max-width: 900px; margin: 0 auto; padding: 20px; }
h1 { text-align: center; margin-bottom: 20px; color: #1a1a1a; }
.search-box { display: flex; gap: 10px; margin-bottom: 20px; }
.search-box input { flex: 1; padding: 12px 16px; border: 2px solid #ddd; border-radius: 8px; font-size: 16px; outline: none; }
.search-box input:focus { border-color: #4a90d9; }
.search-box button { padding: 12px 24px; background: #4a90d9; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
.search-box button:hover { background: #357abd; }
.btn-upload { padding: 12px 18px; background: #34a853; color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; }
.btn-upload:hover { background: #2d9248; }
.btn-upload.dragover { outline: 3px dashed #34a853; outline-offset: 2px; }
.stats { text-align: center; color: #666; margin-bottom: 15px; font-size: 14px; }
.result-item { background: white; border-radius: 8px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.result-item:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.result-title { font-size: 16px; font-weight: 600; color: #1a73e8; margin-bottom: 6px; }
.result-path { font-size: 13px; color: #666; margin-bottom: 8px; }
.result-snippet { font-size: 14px; color: #444; line-height: 1.5; }
.result-cat { display: inline-block; padding: 2px 8px; background: #e8f0fe; color: #1a73e8; border-radius: 4px; font-size: 12px; margin-right: 8px; }
.btn-del { float: right; padding: 4px 12px; background: #fff; color: #d93025; border: 1px solid #d93025; border-radius: 6px; font-size: 12px; cursor: pointer; }
.btn-del:hover { background: #d93025; color: white; }
.loading { text-align: center; padding: 40px; color: #666; }
.error { text-align: center; padding: 20px; color: #d32f2f; }
.empty { text-align: center; padding: 40px; color: #999; }
.cat-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; justify-content: center; }
.cat-btn { padding: 6px 14px; background: white; border: 1px solid #ddd; border-radius: 20px; font-size: 13px; cursor: pointer; transition: all 0.2s; }
.cat-btn:hover, .cat-btn.active { background: #4a90d9; color: white; border-color: #4a90d9; }
#toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: #333; color: white; padding: 10px 20px; border-radius: 8px; font-size: 14px; display: none; z-index: 9; }
#content-area { margin-top: 20px; }
#content-area pre { background: #f8f9fa; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.back-btn { display: inline-block; margin-bottom: 15px; padding: 8px 16px; background: #eee; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
.back-btn:hover { background: #ddd; }
</style>
</head>
<body>
<div class="container">
  <h1>📚 docs-search</h1>
  <div class="search-box">
    <input type="text" id="search-input" placeholder="输入关键词搜索..." autocomplete="off">
    <button onclick="doSearch()">搜索</button>
    <button class="btn-upload" id="upload-btn" title="上传 .md 文档（也可拖拽到本按钮）">⬆ 上传</button>
    <input type="file" id="file-input" accept=".md" multiple hidden>
  </div>
  <div class="stats" id="stats"></div>
  <div class="cat-list" id="cat-list"></div>
  <div id="content-area">
    <div class="empty">输入关键词开始搜索，或选择分类浏览</div>
  </div>
</div>
<div id="toast"></div>
<script>
let currentCat = '';

const $ = id => document.getElementById(id);
const escHtml = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

function toast(msg, ms=2500) {
  const t = $('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.style.display = 'none', ms);
}

$('search-input').addEventListener('keypress', e => { if (e.key === 'Enter') doSearch(); });

// ---- 上传（点击选择 / 拖拽 .md 文件）----
const upBtn = $('upload-btn');
upBtn.addEventListener('click', () => $('file-input').click());
['dragover', 'dragenter'].forEach(ev => upBtn.addEventListener(ev, e => { e.preventDefault(); upBtn.classList.add('dragover'); }));
['dragleave', 'drop'].forEach(ev => upBtn.addEventListener(ev, e => { e.preventDefault(); upBtn.classList.remove('dragover'); }));
upBtn.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
$('file-input').addEventListener('change', e => uploadFiles([...e.target.files]));

async function uploadFiles(files) {
  const mds = files.filter(f => f.name.toLowerCase().endsWith('.md'));
  if (!mds.length) { toast('请选择 .md 文件'); return; }
  for (const f of mds) {
    if (f.size > 10 * 1024 * 1024) { toast(`跳过（超过 10MB）: ${f.name}`); continue; }
    try {
      const text = await f.text();
      const r = await fetch('/api/upload?filename=' + encodeURIComponent(f.name),
                            { method: 'POST', body: text });
      const data = await r.json();
      if (data.error) { toast(`上传失败: ${escHtml(data.error)}`); return; }
      toast(`已上传: ${data.path}（索引 ${data.count} 篇）`);
    } catch (e) { toast('上传失败: ' + escHtml(e.message)); return; }
  }
  $('file-input').value = '';
  loadCategories();
  if (currentCat) filterCat(currentCat);
}

// ---- 统计与分类 ----
async function loadCategories() {
  try {
    const r = await fetch('/api/stats');
    const data = await r.json();
    $('stats').textContent = `共 ${data.count} 个文档 | 最后更新: ${data.updated}`;
    const cats = [...new Set(data.categories || [])].sort();
    const list = $('cat-list');
    list.innerHTML = `<button class="cat-btn ${currentCat === '' ? 'active' : ''}" data-cat="">全部</button>`;
    cats.forEach(c => {
      list.innerHTML += `<button class="cat-btn ${currentCat === c ? 'active' : ''}" data-cat="${escHtml(c)}">${escHtml(c)}</button>`;
    });
  } catch (e) { $('stats').textContent = '加载失败'; }
}

// ---- 搜索 ----
async function doSearch() {
  const q = $('search-input').value.trim();
  if (!q) return;
  const area = $('content-area');
  area.innerHTML = '<div class="loading">搜索中...</div>';
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q) + '&cat=' + encodeURIComponent(currentCat));
    const data = await r.json();
    if (data.error) { area.innerHTML = '<div class="error">' + escHtml(data.error) + '</div>'; return; }
    if (!data.results.length) { area.innerHTML = '<div class="empty">未找到相关文档</div>'; return; }
    let html = '<div class="stats">' + data.results.length + ' 条结果</div>';
    data.results.forEach(d => {
      html += `<div class="result-item">
        ${d.path.startsWith('uploads/') ? `<button class="btn-del" data-del="${escHtml(d.path)}">删除</button>` : ''}
        <div class="result-title">${escHtml(d.title)}</div>
        <div class="result-path"><span class="result-cat">${escHtml(d.cat)}</span>${escHtml(d.path)}</div>
        <div class="result-snippet">${escHtml(d.snippet)}...</div>
      </div>`;
    });
    area.innerHTML = html;
  } catch (e) { area.innerHTML = '<div class="error">搜索失败: ' + escHtml(e.message) + '</div>'; }
}

// ---- 分类浏览 ----
async function filterCat(cat) {
  currentCat = cat;
  document.querySelectorAll('.cat-btn').forEach(b => b.classList.toggle('active', b.dataset.cat === cat));
  const area = $('content-area');
  area.innerHTML = '<div class="loading">加载中...</div>';
  try {
    const r = await fetch('/api/list?cat=' + encodeURIComponent(cat));
    const data = await r.json();
    if (data.error) { area.innerHTML = '<div class="error">' + escHtml(data.error) + '</div>'; return; }
    let html = '<div class="stats">' + data.docs.length + ' 个文档</div>';
    data.docs.forEach(d => {
      html += `<div class="result-item">
        ${d.path.startsWith('uploads/') ? `<button class="btn-del" data-del="${escHtml(d.path)}">删除</button>` : ''}
        <div class="result-title" style="cursor:pointer" data-doc="${escHtml(d.path)}">${escHtml(d.title)}</div>
        <div class="result-path"><span class="result-cat">${escHtml(d.cat)}</span>${escHtml(d.path)} (${(d.size/1024).toFixed(1)}KB)</div>
      </div>`;
    });
    area.innerHTML = html;
  } catch (e) { area.innerHTML = '<div class="error">加载失败</div>'; }
}

// ---- 文档详情 ----
async function showDoc(path) {
  const area = $('content-area');
  area.innerHTML = '<div class="loading">加载中...</div>';
  try {
    const r = await fetch('/api/show?' + new URLSearchParams({ path }));
    const data = await r.json();
    if (data.error) { area.innerHTML = '<div class="error">' + escHtml(data.error) + '</div>'; return; }
    area.innerHTML = `<button class="back-btn" data-back="1">← 返回</button>
      <div class="result-item">
        ${data.path.startsWith('uploads/') ? `<button class="btn-del" data-del="${escHtml(data.path)}">删除</button>` : ''}
        <div class="result-title">${escHtml(data.title)}</div>
        <div class="result-path"><span class="result-cat">${escHtml(data.cat)}</span>${escHtml(data.path)} (${(data.size/1024).toFixed(1)}KB)</div>
        <pre>${escHtml(data.body)}</pre>
      </div>`;
  } catch (e) { area.innerHTML = '<div class="error">加载失败</div>'; }
}

async function deleteDoc(path) {
  if (!confirm('删除 ' + path + ' ?')) return;
  try {
    const r = await fetch('/api/delete?path=' + encodeURIComponent(path), { method: 'POST' });
    const data = await r.json();
    if (data.error) { toast('删除失败: ' + escHtml(data.error)); return; }
    toast(`已删除: ${path}（索引 ${data.count} 篇）`);
    loadCategories();
    filterCat(currentCat);
  } catch (e) { toast('删除失败: ' + escHtml(e.message)); }
}

// ---- 事件委托（避免内联 onclick 与转义问题）----
document.addEventListener('click', e => {
  const del = e.target.closest('[data-del]');
  if (del) { deleteDoc(del.dataset.del); return; }
  const doc = e.target.closest('[data-doc]');
  if (doc) { showDoc(doc.dataset.doc); return; }
  const cat = e.target.closest('[data-cat]');
  if (cat) { filterCat(cat.dataset.cat); return; }
  if (e.target.closest('[data-back]')) { filterCat(currentCat); }
});

loadCategories();
</script>
</body>
</html>
"""


class DocsSearchServer(HTTPServer):
    # Windows 下 SO_REUSEADDR 允许多进程重复 bind 同一端口，必须禁用
    allow_reuse_address = sys.platform != "win32"


def make_handler(docs_dir, db_path, auth=None):
    auth_cfg = auth or AuthConfig()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默访问日志

        # ---------- 认证（所有路径统一拦截）----------
        def _deny(self):
            body = json.dumps({"error": "unauthorized（需要认证: Bearer token 或 Basic）"}, ensure_ascii=False).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("WWW-Authenticate", 'Basic realm="docs-search"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return auth_cfg.check(self.headers.get("Authorization"))

        # ---------- GET ----------
        def do_GET(self):
            if not self._authorized():
                self._deny()
                return
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", HTML_TEMPLATE.encode("utf-8"))
            elif parsed.path.startswith("/api/"):
                self._api_get(parsed.path, urllib.parse.parse_qs(parsed.query))
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _api_get(self, path, params):
            if path == "/api/stats":
                n, _ = ensure_index(docs_dir, db_path)
                meta = load_meta(db_path)
                cats = set()
                try:
                    c = get_conn(db_path)
                    for row in c.execute("SELECT DISTINCT cat FROM docs"):
                        cats.add(row[0])
                    c.close()
                except Exception:  # noqa: BLE001, S110 -- 库不可用时分类列表降级为空
                    pass
                self._json(200, {"count": n, "updated": meta.get("updated", "?"), "categories": sorted(cats)})

            elif path == "/api/search":
                q = params.get("q", [""])[0].strip()
                cat = params.get("cat", [""])[0]
                if not q:
                    self._json(200, {"error": "请输入关键词"})
                    return
                ensure_index(docs_dir, db_path)
                c = get_conn(db_path)
                conditions, pargs = [], []
                for kw in q.split():
                    conditions.append("(title LIKE ? OR body LIKE ?)")
                    pargs.extend([f"%{kw}%", f"%{kw}%"])
                sql = f"SELECT path, cat, title, body FROM docs WHERE {' AND '.join(conditions)}"
                if cat:
                    sql += " AND cat = ?"
                    pargs.append(cat)
                sql += " LIMIT 20"
                rows = c.execute(sql, pargs).fetchall()
                c.close()
                results = [
                    {"path": p, "cat": c, "title": t, "snippet": b[:150].replace("\n", " ")} for p, c, t, b in rows
                ]
                self._json(200, {"results": results})

            elif path == "/api/list":
                ensure_index(docs_dir, db_path)
                cat = params.get("cat", [""])[0]
                c = get_conn(db_path)
                if cat:
                    rows = c.execute("SELECT path, title, size FROM docs WHERE cat=? ORDER BY title", (cat,)).fetchall()
                else:
                    rows = c.execute("SELECT path, title, size FROM docs ORDER BY cat, title").fetchall()
                c.close()
                self._json(200, {"docs": [{"path": r[0], "title": r[1], "size": r[2]} for r in rows]})

            elif path == "/api/show":
                ensure_index(docs_dir, db_path)
                p = params.get("path", [""])[0]
                c = get_conn(db_path)
                row = c.execute("SELECT title, body, size, cat FROM docs WHERE path=?", (p,)).fetchone()
                c.close()
                if not row:
                    self._json(200, {"error": "文档不存在"})
                    return
                self._json(200, {"title": row[0], "body": row[1], "size": row[2], "cat": row[3], "path": p})
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        # ---------- POST ----------
        def do_POST(self):
            if not self._authorized():
                self._deny()
                return
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/upload":
                self._api_upload(params)
            elif parsed.path == "/api/delete":
                self._api_delete(params)
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _read_body(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return None, (411, {"error": "missing Content-Length"})
            if length <= 0:
                return None, (400, {"error": "empty body"})
            if length > MAX_UPLOAD_BYTES:
                return None, (413, {"error": f"文件过大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）"})
            return self.rfile.read(length), None

        def _api_upload(self, params):
            filename = sanitize_filename(params.get("filename", [""])[0])
            if not filename:
                self._json(400, {"error": "文件名非法（仅支持 .md，且不含路径部分）"})
                return
            if_exists = params.get("if_exists", ["error"])[0]
            if if_exists not in IF_EXISTS_CHOICES:
                self._json(400, {"error": f"未知 if_exists 取值: {if_exists!r}（可选: error/overwrite/keep）"})
                return
            body, err = self._read_body()
            if err:
                self._json(err[0], err[1])
                return
            try:
                body.decode("utf-8")
            except UnicodeDecodeError:
                self._json(400, {"error": "文件必须是 UTF-8 文本"})
                return
            target, msg = resolve_upload_target(docs_dir, filename, if_exists)
            if target is None:
                self._json(409, {"error": msg})  # 同名冲突（默认策略）
                return
            target.write_bytes(body)
            n, _ = ensure_index(docs_dir, db_path)
            self._json(200, {"ok": True, "path": f"uploads/{target.name}", "count": n})

        def _api_delete(self, params):
            rel = params.get("path", [""])[0].replace("\\", "/")
            uploads_root = (docs_dir / "uploads").resolve()
            target = (docs_dir / rel).resolve()
            # 仅允许删除 uploads/ 下的文件，且防路径穿越
            if uploads_root not in target.parents or target == uploads_root:
                self._json(403, {"error": "仅允许删除 uploads/ 下的文档"})
                return
            if not target.exists():
                self._json(404, {"error": "文档不存在"})
                return
            try:
                target.unlink()
            except OSError as e:
                self._json(500, {"error": f"删除失败: {e}"})
                return
            n, _ = ensure_index(docs_dir, db_path)
            self._json(200, {"ok": True, "deleted": rel, "count": n})

        # ---------- 输出 ----------
        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, data):
            self._send(code, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode("utf-8"))

    return Handler


def main():
    win_utf8()
    parser = argparse.ArgumentParser(description="docs-search-web: 文档搜索 Web UI（含上传接口）")
    parser.add_argument("dir", nargs="?", default=None, help="文档根目录（默认 ./docs 或 $DOCS_SEARCH_DIR）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，勿暴露公网）")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--workspace", default=None, help=f"工作空间名（可选，索引库按 {ENV_WORKSPACE} 分割；不填=默认库）")
    parser.add_argument("--token", default=None, help=f"启用 Bearer 认证（默认 ${ENV_TOKEN}）")
    parser.add_argument("--user", default=None, help=f"启用 Basic 认证用户名（默认 ${ENV_USER}；与 --password 成对）")
    parser.add_argument("--password", default=None, help=f"Basic 认证密码（默认 ${ENV_PASSWORD}）")
    args = parser.parse_args()

    token = args.token or os.environ.get(ENV_TOKEN)
    user = args.user or os.environ.get(ENV_USER)
    password = args.password or os.environ.get(ENV_PASSWORD)
    if token and (user or password):
        parser.error("--token 与 --user/--password 互斥，只能启用一种认证方式")
    if bool(user) != bool(password):
        parser.error("--user 与 --password 必须成对提供")
    auth = AuthConfig(token, user, password)

    guard = validate_listen_config(args.host, auth)
    if guard:
        print(f"错误: {guard}", file=sys.stderr)
        print("  例: docs-search-web <DIR> --host 0.0.0.0 --token <TOKEN>", file=sys.stderr)
        sys.exit(1)

    docs_dir = resolve_docs_dir(args.dir)
    db_path = resolve_db_path(docs_dir, workspace=resolve_workspace(args.workspace))
    if not docs_dir.exists():
        print(f"错误: 文档目录不存在: {docs_dir}")
        print(f"请指定目录参数，或设置环境变量 {('DOCS_SEARCH_DIR')}")
        sys.exit(1)

    print(f"文档目录: {docs_dir}")
    ws = resolve_workspace(args.workspace)
    if ws:
        print(f"工作空间: {ws}")
    n, updated = ensure_index(docs_dir, db_path)
    print(f"索引就绪: {n} 个文档{'（已重建）' if updated else ''}")
    if auth.enabled:
        print("认证: 已启用（Bearer）" if auth.token else "认证: 已启用（Basic）")

    server = DocsSearchServer((args.host, args.port), make_handler(docs_dir, db_path, auth))
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"服务已启动: {url}")
    print("按 Ctrl+C 停止")
    if not args.no_browser:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
