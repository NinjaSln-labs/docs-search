# docs-search Agent 操作手册

> 机器可读的操作协议。Agent 直接执行，无需人类解释。
> 机器可读索引（工具元数据/API/错误表的 JSON 形式）：[AGENT-INDEX.json](AGENT-INDEX.json)

## 1. 工具身份

```json
{
  "name": "docs-search",
  "pypi": "docs-search",
  "version": "1.1.0",
  "runtime": "Python >= 3.10",
  "dependencies": [],
  "license": "MIT",
  "entry_points": ["docs-search", "docs-search-web", "docs-search-mcp"],
  "network": "本地模式无出站请求(纯 stdio),HTTP 服务默认仅绑定 127.0.0.1;MCP 远程模式(--url/$DOCS_SEARCH_URL)会向指定服务发 HTTP 请求"
}
```

## 2. 何时选用

- 需要检索本地 Markdown 文档库（个人知识库 / Agent 记忆库 / 调研快照）
- 需要"写进去就能搜到"的持久化文档记忆（上传接口 + 自动索引）
- 约束场景：不能装第三方依赖、不能联网、资源受限（纯 stdlib）

不适用：需要语义/向量检索（本项目是关键词 AND 匹配）；需要多用户/公网服务。

## 3. 安装与自检

```bash
pip install docs-search          # 或零安装：python scripts/docs-search.py
docs-search status --dir <DIR>   # 自检：能看到 records 即可用
```

## 4. 标准操作流程

### 4.1 检索文档库（最常用）

```bash
# 方式 A：CLI（一次性查询）
docs-search search "关键词1 关键词2" --dir <DIR> -n 5
```

```bash
# 方式 B：HTTP API（推荐给常驻 Agent——起一次服务，全程复用）
docs-search-web <DIR> --no-browser --port 8765 &
curl 'http://127.0.0.1:8765/api/search?q=关键词1%20关键词2'
curl 'http://127.0.0.1:8765/api/show?path=infra/mcp.md'   # 取全文
```

- 多关键词空格分隔 = AND；URL 里空格编码为 `%20`
- 搜索前服务端自动增量重建，**无需手动维护索引**
- 返回 snippet 只有 150 字符，要全文跟 `/api/show`

### 4.2 写入文档记忆（上传）

```bash
curl -X POST 'http://127.0.0.1:8765/api/upload?filename=session-2026-09-07.md' \
     --data-binary @session.md
# → {"ok": true, "path": "uploads/session-2026-09-07.md", "count": 136}
```

- 上传即索引（响应里的 `count` 是新总数），立即可搜
- 约束：仅 `.md`、≤10MB、UTF-8；文件名消毒，路径穿越变 basename
- 同名自动 `-1`/`-2` 去重——响应的 `path` 才是真实路径，用它做后续操作
- Agent 典型模式：会话结束 → 把结论写成 md → 上传 → 下个会话可检索

### 4.3 删除已上传文档

```bash
curl -X POST 'http://127.0.0.1:8765/api/delete?path=uploads/session-2026-09-07.md'
```

仅限 `uploads/` 下；库内文档返回 403（设计如此，勿重试）。

### 4.4 枚举与统计

```bash
curl 'http://127.0.0.1:8765/api/stats'        # 总数/更新时间/分类列表
curl 'http://127.0.0.1:8765/api/list'         # 全部文档（或 ?cat=分类）
```

### 4.5 MCP 接入（推荐给支持 MCP 的 Agent：Cursor / ZCode / Qoder / DSH）

内置零依赖 MCP stdio server（`docs-search-mcp`），工具逻辑与 HTTP API 同源：

- 工具：`docs_search` / `docs_read` / `docs_write` / `docs_delete` / `docs_info`（参数与语义同 §4.1–§4.4）
- 协议：JSON-RPC 2.0 over stdio，按行分隔；搜索前自动增量重建，无需手动维护索引
- 两种模式：本地模式（`--dir`，读本地目录，零网络）；远程模式（`--url http://ip:port` 或
  `$DOCS_SEARCH_URL`，代理到已运行的 docs-search 服务，不读本地目录——服务已启动/异机共享时用）
- 远程认证：远端服务启用认证时，`--token`（Bearer）或 `--user/--password`（Basic），
  环境变量 `DOCS_SEARCH_TOKEN` / `DOCS_SEARCH_USER` / `DOCS_SEARCH_PASSWORD` 回退；凭据错误探活即失败退出
- pi 无内置 MCP：用仓库内 `integrations/pi/docs-search.ts` 扩展桥接同一 server（远程模式设 `DOCS_SEARCH_URL`）

五家 agent 的完整配置（含已核实的官方配置格式）见 **[MCP.md](MCP.md)**。Cursor 最小示例：

```json
{ "mcpServers": { "docs-search": { "command": "docs-search-mcp", "args": ["--dir", "/path/to/docs"] } } }
```

## 5. 错误处理协议

| 现象 | 原因 | Agent 处理 |
|---|---|---|
| `索引库被占用，无法重建` | 另一进程持写锁 | 串行化：同一目录只用一个写入口（CLI 与 Web 二选一触发重建） |
| `{"error":"请输入关键词"}` | `q` 为空 | 补参数重试 |
| `{"error":"文件必须是 UTF-8 文本"}` | 上传体非 UTF-8 | 先转码再传 |
| `{"error":"文件名非法..."}` | filename 非 .md / 含路径 | 修正文件名（只传 basename） |
| `{"error":"仅允许删除 uploads/ 下的文档"}` | 删库内文档 | 预期行为，不要重试 |
| `{"error":"文档不存在"}` | path 未命中 | 先 `/api/list` 拿准确相对路径 |
| CLI 无输出且 exit 0 | 未装包且不在仓库目录 | `pip install docs-search` 后重试 |
| 全部请求 connection refused | 服务没起 | 先起 `docs-search-web <DIR> --no-browser` |

## 6. 路径与状态语义

- 文档目录优先级：`--dir` > `$DOCS_SEARCH_DIR` > `./docs`
- 索引库：`~/.docs-search/<目录绝对路径哈希>/index.db`，按目录隔离
- **不同目录 = 不同库**；跨目录搜索要起多个服务实例（不同端口）或分别跑 CLI
- 重建是幂等的（`DROP TABLE IF EXISTS`），索引损坏时 `docs-search index` 重来即可
- 索引是派生数据：备份文档目录即可，无需备份 `~/.docs-search/`

## 7. 安全红线（Agent 必读）

1. 服务无鉴权：不要把 `--host` 改成 `0.0.0.0`；只在 `127.0.0.1` 上用
2. 上传内容会进入索引并对本机所有本机进程可见——**不要上传含密钥/隐私的原始数据**（先脱敏）
3. 删除接口只碰 `uploads/`；对库内文档的修改请走文件系统（用户自己的职责范围）
4. 漏洞勿公开披露：https://github.com/NinjaSln-labs/docs-search/security/advisories/new

## 8. 常见任务配方

**Q: 建立一个"可检索的会话记忆库"**

```bash
mkdir -p ~/agent-memory/docs
docs-search-web ~/agent-memory --no-browser --port 8765 &
# 每次会话结束：
curl -X POST 'http://127.0.0.1:8765/api/upload?filename=2026-09-07-task-x.md' --data-binary @summary.md
# 下次会话开始：
curl 'http://127.0.0.1:8765/api/search?q=task-x%20结论'
```

**Q: 大批量导入已有 md 文件**

直接复制进文档目录（保持子目录分类），首次搜索/访问时自动全量索引。无需逐个上传。

**Q: 校验服务健康**

```bash
curl -sf 'http://127.0.0.1:8765/api/stats' | python -c "import json,sys; d=json.load(sys.stdin); print(d['count'])"
```

## 9. 快速参考卡

```bash
# 安装 / 自检
pip install docs-search && docs-search status --dir ./docs

# CLI 三板斧
docs-search index --dir ./docs
docs-search search "kw1 kw2" --dir ./docs -n 5
docs-search show path/to/file.md --dir ./docs

# 服务 + API
docs-search-web ./docs --no-browser &
curl 'http://127.0.0.1:8765/api/search?q=kw'
curl -X POST 'http://127.0.0.1:8765/api/upload?filename=a.md' --data-binary @a.md
curl -X POST 'http://127.0.0.1:8765/api/delete?path=uploads/a.md'
```

## 10. 反馈通道

- 工具缺陷/建议：https://github.com/NinjaSln-labs/docs-search/issues
- 本文档与 AGENT-INDEX.json 不一致时，以代码为准并欢迎提 Issue 指出

---

*docs-search v1.1.0 · 协议版本随版本号同步 · [文档站首页](index.html)*
