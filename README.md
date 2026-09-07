# docs-search

零依赖的本地文档搜索引擎。纯 Python 标准库，SQLite 索引，毫秒级检索。

A zero-dependency local document search engine. Pure Python stdlib, SQLite index, millisecond-level retrieval.

## 特性 / Features

- **零依赖** — 纯 Python 标准库（3.10+），无需 pip install
- **快** — SQLite FTS 索引，检索 < 50ms
- **自动索引** — 搜索前自动检测文件变更并增量重建
- **AI Agent 对接** — 内置 MCP stdio server（Cursor / ZCode / Qoder / DSH 等即插即用）+ pi 扩展；支持本地模式与远程模式（`--url` 连已运行的服务）
- **Web UI** — 内置搜索界面 + 拖拽上传 .md 文档
- **多库隔离** — 不同文档目录各自独立索引，可并存
- **不绑定路径** — 文档目录由参数/环境变量指定，不写死任何本地路径
- **跨平台** — Windows / macOS / Linux

## 📖 文档站 / Documentation

**https://ninjasln-labs.github.io/docs-search/**

- [人类使用手册](https://ninjasln-labs.github.io/docs-search/#doc=HUMAN-GUIDE.md)（安装 / CLI / Web / FAQ）
- [Agent 接入指南](https://ninjasln-labs.github.io/docs-search/#doc=MCP.md)（MCP server + Cursor / ZCode / Qoder / DSH / pi 配置）
- [Agent 操作手册](https://ninjasln-labs.github.io/docs-search/#doc=AGENT-GUIDE.md) + [AGENT-INDEX.json](https://ninjasln-labs.github.io/docs-search/AGENT-INDEX.json)（机器可读索引）
- [Web API 参考](https://ninjasln-labs.github.io/docs-search/#doc=API.md)

## 快速开始 / Quick Start

```bash
# 1. 索引一个文档目录（默认 ./docs，也可用 --dir 指定）
python scripts/docs-search.py index --dir /path/to/your/docs

# 2. 搜索
python scripts/docs-search.py search "关键词" --dir /path/to/your/docs

# 3. 启动 Web UI（含上传接口）
python scripts/docs-search-web.py /path/to/your/docs
# 访问 http://127.0.0.1:8765
```

## 路径解析规则

| 目标 | 优先级 |
|------|--------|
| 文档目录 | `--dir` 参数 > 环境变量 `DOCS_SEARCH_DIR` > `./docs` |
| 索引库 | `--db` 参数（CLI）> 环境变量 `DOCS_SEARCH_DB` > `~/.docs-search/<目录哈希>/index.db` |

索引库按文档目录哈希隔离——多个文档目录可以各自拥有独立索引，互不干扰。

## CLI

```bash
python scripts/docs-search.py index    [--dir DIR]   # 重建索引
python scripts/docs-search.py search "关键词" [--dir DIR] [-n 8]  # 多关键词 AND 搜索
python scripts/docs-search.py list     [--dir DIR]   # 列出所有文档
python scripts/docs-search.py show <path> [--dir DIR] # 显示文档内容
python scripts/docs-search.py status   [--dir DIR]   # 查看索引状态
python scripts/docs-search.py upload <file.md> [--dir DIR]  # 复制 .md 到文档库 uploads/ 并重建索引
python scripts/docs-search.py open <path> [--dir DIR]  # 用系统默认程序打开
```

Windows 下可用 `scripts/docs-search.bat`。

## Web API

启动：`python scripts/docs-search-web.py [DIR] [--port 8765] [--host 127.0.0.1] [--no-browser]`

| 方法 | 端点 | 说明 |
|------|------|------|
| GET  | `/api/stats` | 统计信息 `{count, updated, categories}` |
| GET  | `/api/search?q=关键词&cat=` | 搜索（多关键词 AND） |
| GET  | `/api/list?cat=` | 列出文档 |
| GET  | `/api/show?path=x.md` | 文档内容 |
| POST | `/api/upload?filename=x.md` | 上传文档（raw body = UTF-8 文本） |
| POST | `/api/delete?path=uploads/x.md` | 删除 `uploads/` 下已上传文档 |

上传示例：

```bash
curl -X POST "http://127.0.0.1:8765/api/upload?filename=notes.md" \
     --data-binary @notes.md
```

上传的文件存入 `<文档目录>/uploads/` 并自动进入索引；同名自动加 `-1`、`-2` 后缀。

## AI Agent 接入（MCP）

内置零依赖 MCP stdio server，让 AI agent 直接检索/写入文档库：

```bash
# Cursor（~/.cursor/mcp.json）
{ "mcpServers": { "docs-search": { "command": "docs-search-mcp", "args": ["--dir", "/path/to/docs"] } } }

# Qoder CLI（-s user 全局；中国版额度在 qodercn，与国际版账号不通用）
qodercn mcp add -s user docs-search -- docs-search-mcp --dir /path/to/docs
```

暴露 5 个工具：`docs_search` / `docs_read` / `docs_write` / `docs_delete` / `docs_info`。
ZCode（Settings → MCP Servers）、DSH（`@deepseek-ai/dsh-mcp-client` 插件行）配置及 pi 扩展安装，
见 [Agent 接入指南](docs/MCP.md)。

已有 docs-search 服务在运行时（本机或其他机器，接口与 `/api/*` 相同），MCP 可直接连它、不读本地目录：

```bash
# Cursor（远程模式——服务已启动/异机共享时用）
{ "mcpServers": { "docs-search": { "command": "docs-search-mcp", "args": ["--url", "http://192.168.1.10:8765"] } } }
# 或环境变量: DOCS_SEARCH_URL=http://192.168.1.10:8765 docs-search-mcp
```

## 安全说明 / Security

- 服务默认仅监听 `127.0.0.1`，**请勿用 `--host 0.0.0.0` 暴露到公网**（接口无鉴权）
- 上传仅接受 `.md` 文件、单文件 ≤ 10MB、文件名经过消毒（防路径穿越）
- 删除接口仅允许操作 `uploads/` 目录内的文件

## 开发 / Development

```bash
pip install -r requirements.lock -e .   # 可编辑安装 + 锁定的开发工具链
python scripts/verify.py        # 验证链单源：ruff + pytest（单元 + CLI E2E + Web API）
ruff check src/ tests/ scripts/  # lint
git config core.hooksPath .githooks  # 启用 pre-commit 验证链
```

工程结构见 [DEVELOPMENT.md](DEVELOPMENT.md)，贡献规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，AI 协作纪律见 [AGENTS.md](AGENTS.md)。English docs: [README.en.md](README.en.md)

## 许可证 / License

MIT — 见 [LICENSE](LICENSE)
