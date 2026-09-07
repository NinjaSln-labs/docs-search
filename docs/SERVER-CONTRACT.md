# docs-search 兼容服务端契约（Server Contract）

> **给谁看**：想自己实现一个 docs-search 兼容服务的开发者（任何语言/框架）。
> 远程模式（`docs-search-mcp --url http://ip:port` / `DOCS_SEARCH_URL`）的承诺是
> "只要服务有和 docs-search 一样的接口就能连"——本文件就是那份**接口规范**。
>
> **与其它文档的关系**：本文件是**实现方**视角；调用方视角见 [API.md](API.md)（官方服务怎么用）；
> 代理/桥接场景见 [MCP.md](MCP.md) 远程模式节。**唯一权威实现**是
> [src/docs_search/web.py](../src/docs_search/web.py)，任何语言均可按本契约复刻。

---

## 1. 最小端点集

兼容服务**必须**实现以下 6 个端点；缺哪个，对应的 MCP 工具/客户端方法就报错：

| 方法 | 路径 | 对应 MCP 工具 |
|---|---|---|
| `GET` | `/api/stats` | `docs_info mode=stats`（**同时是启动探活端点，必须实现**） |
| `GET` | `/api/search` | `docs_search` |
| `GET` | `/api/list` | `docs_info mode=list` |
| `GET` | `/api/show` | `docs_read` |
| `POST` | `/api/upload` | `docs_write` |
| `POST` | `/api/delete` | `docs_delete` |

**启动探活**：MCP 远程模式启动时会先 `GET /api/stats`——返回的 JSON 若含 `error` 键或
连接失败/非 JSON，MCP 立即报错退出。所以 `/api/stats` 是硬性要求，其余端点缺失只影响对应工具。

## 2. 全局约定

- 响应一律 `Content-Type: application/json; charset=utf-8`，body 为**单个 JSON 对象**
  （数组/字符串等一律视为不兼容，客户端抛"服务响应格式异常"）
- **业务错误**：返回 JSON `{"error": "<可读提示>"}`，状态码任意（官方用 400/401/403/404/409/413/500）；
  客户端把 `error` 字段原样透传给 agent
- **认证失败**（401）：客户端提示"认证失败——检查 --token 或 --user/--password"
- **非 JSON / 非对象响应**：视为不兼容（连接成功但协议不符）
- 未知查询参数必须**忽略**（客户端会附带 `ws`、`if_exists` 等，老服务不应报错）
- 上传 body 为**原始文本**（`text/plain; charset=utf-8`，即文件内容），**不是** multipart/form-data
- CORS 头 `Access-Control-Allow-Origin: *`（Web UI 需要；纯 API 场景可省略）

## 3. 端点契约

### GET /api/stats
查询参数：`ws`（可选）
成功：`{"count": <int>, "updated": "<iso 时间>", "categories": ["<分类名>", ...]}`
业务错误：`{"error": "..."}`（如库不可用）

### GET /api/search
查询参数：`q`（必填，多关键词空格分隔 AND）、`cat`（可选分类过滤）、`ws`（可选）
成功：`{"results": [{"path": "<相对路径>", "cat": "<分类>", "title": "<标题>", "snippet": "<前 150 字去换行>"}, ...]}`
`q` 为空：`{"error": "请输入关键词"}`（200）
`ws=all`：跨全部库聚合，每个结果附加 `ws` 来源字段（默认库为 `""`），顶层加 `"workspace": "all"`

### GET /api/list
查询参数：`cat`（可选）、`ws`（可选）
成功：`{"docs": [{"path": "<相对路径>", "title": "<标题>", "size": <int>}, ...]}`
`ws=all`：聚合，每条附加 `ws` 来源字段，顶层加 `"workspace": "all"`

### GET /api/show
查询参数：`path`（必填，相对路径）、`ws`（可选）
成功：`{"title": "...", "body": "<全文>", "size": <int>, "cat": "...", "path": "<原样返回>"}`
未命中：`{"error": "文档不存在"}`（200 或 404 均可，客户端都透传）

### POST /api/upload
查询参数：`filename`（必填，仅 `.md` 的 basename）、`if_exists`（可选，`error`/`overwrite`/`keep`，默认 `error`）、`ws`（可选）
请求体：raw UTF-8 文本（≤10MB）
成功：`{"ok": true, "path": "uploads/<落盘名>", "count": <int>}`——`path` 是**实际分配**的路径
   （同名 `keep` 时为 `-1/-2` 后缀），客户端以它为准做后续操作
业务错误：`{"error": "文件已存在: uploads/x.md（同名冲突……）"}`（官方 409）/ 非法文件名 400 等

### POST /api/delete
查询参数：`path`（必填，必须 `uploads/` 下）、`ws`（可选）
成功：`{"ok": true, "deleted": "<相对路径>", "count": <int>}`
业务错误：`{"error": "仅允许删除 uploads/ 下的文档"}`（403）/ `{"error": "文档不存在"}`（404）等

## 4. 认证（可选，推荐远端部署启用）

- **Bearer**：校验 `Authorization: Bearer <token>`
- **Basic**：校验 `Authorization: Basic <base64(user:password)>`
- 未带/错带凭据 → `401` + `{"error": "unauthorized..."}` + `WWW-Authenticate` 头
- 客户端侧：`docs-search-mcp --token <T>` 或 `--user <U> --password <P>`（环境变量
  `DOCS_SEARCH_TOKEN` / `DOCS_SEARCH_USER` / `DOCS_SEARCH_PASSWORD` 回退）

## 5. workspace 语义

- 所有端点接受可选 `ws=<name>`：操作限定在该 workspace 库内
- `ws=all`（搜索/列表/统计）：跨默认库 + 全部 workspace 聚合，结果带 `ws` 来源字段，
  顶层 `"workspace": "all"`；官方还返回 `workspaces`（库名列表，stats 有）
- 读/写/删端点收到 `ws=all` 应返回 `400 {"error": "workspace=all 仅支持搜索/列表/统计"}`（官方行为）
- **最小实现可暂不支持 `all`**：客户端会把 `ws=all` 当普通库名请求，行为退化为空结果但不报错

## 6. 最小可用子集（渐进兼容）

- **只读服务**（stats/search/list/show）即可支撑 `docs_search` / `docs_read` / `docs_info` 三个工具；
  `docs_write` / `docs_delete` 会收到 `{"error": ...}` 并透传给 agent
- 认证可按需启用：客户端配置了凭据但服务端不校验，凭据被忽略；服务端要认证而客户端未配，
  探活即 401 → MCP 启动失败并提示

## 7. 验收自检

对部署好的自定义服务逐条跑（替换 `BASE` 为服务地址）：

```bash
BASE=http://127.0.0.1:8765
curl "$BASE/api/stats"                                    # {"count":..., "updated":..., "categories":[...]}
curl "$BASE/api/search?q=关键词"                            # {"results":[{path,cat,title,snippet}]}
curl "$BASE/api/list"                                      # {"docs":[{path,title,size}]}
curl "$BASE/api/show?path=x.md"                            # {title,body,size,cat,path} 或 {"error":...}
curl -X POST "$BASE/api/upload?filename=n.md" --data-binary '内容'   # {"ok":true,"path":"uploads/n.md","count":...}
curl -X POST "$BASE/api/delete?path=uploads/n.md"          # {"ok":true,"deleted":"uploads/n.md","count":...}
```

或用官方客户端做端到端验收（探活失败会立即报错，是最快的契约检查）：

```bash
docs-search-mcp --url "$BASE"     # 挂起等待 stdin 即通过；报"无法连接/服务响应不是 JSON"即契约不符
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"docs_search","arguments":{"query":"关键词"}}}' \
  | docs-search-mcp --url "$BASE"
```

## 8. 参考实现

官方实现（唯一权威）：[src/docs_search/web.py](../src/docs_search/web.py)（纯 Python 标准库，
`docs-search-web` 命令，含认证/workspace/同名策略全部语义）。客户端行为见
[src/docs_search/remote.py](../src/docs_search/remote.py)（本契约的逆向推导来源）。

---

## 变更记录

- 2026-09-08：初版（随远程模式 + workspace 能力沉淀，契约取自 remote.py/web.py 实现）
