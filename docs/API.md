# docs-search Web API 参考

> HTTP 服务默认监听 `127.0.0.1:8765`（`docs-search-web <DIR> --no-browser` 启动）。
> 所有响应均为 `application/json; charset=utf-8`，含 CORS 头 `Access-Control-Allow-Origin: *`。
> 安全提醒：默认仅监听回环地址；**监听非回环地址时必须启用认证**，否则服务拒绝启动。

## 端点总览

| 方法 | 端点 | 用途 |
|------|------|------|
| GET  | `/api/stats`  | 索引统计与分类列表 |
| GET  | `/api/search` | 关键词搜索 |
| GET  | `/api/list`   | 枚举文档 |
| GET  | `/api/show`   | 取文档全文 |
| POST | `/api/upload?filename=x.md` | 上传文档 |
| POST | `/api/delete?path=uploads/x.md` | 删除已上传文档 |
| GET  | `/`           | Web UI（HTML） |

---

## GET /api/stats

索引统计。同时会触发一次增量检测（文档有变化则自动重建后再返回）。

**响应**

```json
{
  "count": 135,
  "updated": "2026-09-07T10:00:00.000000",
  "categories": ["code", "infra", "memory", "uploads"]
}
```

- `count`：索引内文档总数
- `categories`：一级子目录名列表（上传的文档归入 `uploads`）

---

## GET /api/search

**参数**

| 参数 | 必填 | 说明 |
|------|------|------|
| `q`  | ✓ | 搜索关键词。多个关键词用空格分隔，语义为 AND（都要命中） |
| `cat` | ✗ | 限定分类（一级子目录名），如 `uploads` |

**示例**

```bash
curl 'http://127.0.0.1:8765/api/search?q=MCP%20协议'
curl 'http://127.0.0.1:8765/api/search?q=上传&cat=uploads'
```

**响应**

```json
{
  "results": [
    {
      "path": "infra/mcp.md",
      "cat": "infra",
      "title": "MCP 协议与 Server 生态手册",
      "snippet": "---  ## 1. MCP 是什么  MCP（Model Context Protocol）..."
    }
  ]
}
```

- 最多返回 20 条；`snippet` 为正文前 150 字符（换行替换为空格）
- 匹配范围：标题或正文，子串匹配（LIKE `%kw%`），无分词、无相关度排序

**错误**

| 响应 | 条件 |
|------|------|
| `{"error": "请输入关键词"}` | `q` 缺失或为空 |

---

## GET /api/list

**参数**

| 参数 | 必填 | 说明 |
|------|------|------|
| `cat` | ✗ | 限定分类；缺省列出全部 |

**响应**

```json
{
  "docs": [
    { "path": "infra/mcp.md", "title": "MCP 协议与 Server 生态手册", "size": 5120 },
    { "path": "uploads/notes.md", "title": "notes", "size": 233 }
  ]
}
```

按 `cat` 分组排序（分类名 → 标题）。`path` 即 `/api/show`、`/api/delete` 所需的相对路径。

---

## GET /api/show

**参数**

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | ✓ | 文档相对路径（从 list/search 获得） |

**响应**

```json
{
  "path": "uploads/notes.md",
  "cat": "uploads",
  "title": "notes",
  "size": 233,
  "body": "文档全文（UTF-8 文本）"
}
```

**错误**

| 响应 | 条件 |
|------|------|
| `{"error": "文档不存在"}` | path 未命中索引 |

---

## POST /api/upload?filename=x.md

上传一个 Markdown 文档。**请求体是文件内容的原始文本**（UTF-8），不是 multipart。

**参数**

| 参数 | 必填 | 说明 |
|------|------|------|
| `filename` | ✓ | 目标文件名。仅 basename（路径部分会被剥离）、必须 `.md` 结尾、≤200 字符 |

**约束**

- 单文件 ≤ 10MB（超出返回 413）
- 内容必须可按 UTF-8 解码（否则 400）
- 危险字符（`\\/:*?"<>|` 与控制符）替换为 `_`；路径穿越降级为 basename
- 目标固定落在 `<文档目录>/uploads/`；同名自动 `-1` … `-999` 去重

**示例**

```bash
curl -X POST 'http://127.0.0.1:8765/api/upload?filename=session-note.md' \
     --data-binary @session-note.md
```

**成功响应**

```json
{ "ok": true, "path": "uploads/session-note.md", "count": 136 }
```

`path` 是落盘后的真实相对路径（重名时带序号），后续删除/检索以它为准。上传即重建索引，立即可搜。

**错误**

| 状态码 | 响应 | 条件 |
|--------|------|------|
| 400 | `{"error": "文件名非法（仅支持 .md，且不含路径部分）"}` | filename 缺失/非 .md/超长 |
| 400 | `{"error": "empty body"}` | 请求体为空 |
| 400 | `{"error": "文件必须是 UTF-8 文本"}` | body 无法按 UTF-8 解码 |
| 413 | `{"error": "文件过大（上限 10MB）"}` | Content-Length 超 10MB 或缺 Content-Length(411) |

---

## POST /api/delete?path=uploads/x.md

删除**已上传**文档（仅限 `uploads/` 目录内）。

**参数**

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | ✓ | 相对路径，必须位于 `uploads/` 内 |

**安全行为**

- 路径穿越检查：解析后的真实路径必须位于 `uploads/` 之内，否则 403（无论文件是否存在，避免探测）
- 删除成功后立即增量重建索引

**成功响应**

```json
{ "ok": true, "deleted": "uploads/x.md", "count": 135 }
```

**错误**

| 状态码 | 响应 | 条件 |
|--------|------|------|
| 403 | `{"error": "仅允许删除 uploads/ 下的文档"}` | 目标不在 uploads/ 内（含穿越尝试） |
| 404 | `{"error": "文档不存在"}` | 文件已不在磁盘 |
| 500 | `{"error": "删除失败: ..."}` | 文件系统错误（如被占用） |

---

## 认证（远端部署）

默认无认证（仅限回环监听）。作为远端服务部署（`--host 0.0.0.0` 或异机访问）时**必须**启用认证，
二选一（凭据也可用环境变量 `DOCS_SEARCH_TOKEN` / `DOCS_SEARCH_USER` / `DOCS_SEARCH_PASSWORD`）：

```bash
docs-search-web <DIR> --host 0.0.0.0 --token <TOKEN>            # Bearer
# 或
docs-search-web <DIR> --host 0.0.0.0 --user admin --password <P> # Basic
```

- 启用后**所有路径**（含 Web UI 页面与全部 `/api/*`）均需认证，未带/错带凭据返回 `401`
  `{"error": "unauthorized（需要认证: Bearer token 或 Basic）"}`，并带 `WWW-Authenticate` 头
- 凭据比较用 `hmac.compare_digest`（防时序侧信道）；`--token` 与 `--user/--password` 互斥
- 请求方式：`Authorization: Bearer <token>` 或 `Authorization: Basic <base64(user:password)>`
- 机器可读配置（MCP 远程模式）见 [MCP.md](MCP.md)

## 并发与一致性说明

- HTTP 服务为单线程串行处理：同目录下不要再用 CLI 触发 `index`，避免索引锁竞争
- 搜索/列表/详情前都会做一次增量检测（目录指纹 = 路径:mtime:大小 的哈希），外部直接改文件也会被感知
- 索引库位置：`~/.docs-search/<目录哈希>/index.db`；重建幂等

## 安全（SQL 注入）

- 所有查询均为**参数化查询**（`?` 占位符 + 参数绑定），`q`/`cat`/`path` 等用户输入按字面值处理，
  不参与 SQL 结构拼接——无注入面；分类/关键词/路径中的 `'`、`--`、`UNION` 等载荷不会改变查询结构
- 上传仅限 `.md`、≤10MB、文件名消毒（防路径穿越）；删除仅限 `uploads/` 目录（tests/test_web.py 安全用例覆盖）

## 版本

- API 随 docs-search 版本发布；破坏性变更会在 CHANGELOG.md 标注
- 机器可读索引：[AGENT-INDEX.json](AGENT-INDEX.json)
