# 开发指南

## 结构速查

```
后端（可独立启动为远端服务，零网络依赖）:
src/docs_search/
  core.py     # 扫描、索引、路径解析、上传文件名安全化（CLI/Web/MCP 共享核心）
  cli.py      # 命令行入口（index/search/list/show/status/upload/open）
  web.py      # HTTP 服务 + Web UI（含 /api/upload、/api/delete、Bearer/Basic 认证）
agent 对接（不依赖本地文档目录——远程模式纯 HTTP 代理）:
src/docs_search/
  mcp.py      # MCP stdio server（JSON-RPC 2.0，供 cursor/zcode/qorder/dsh 对接，见 docs/MCP.md）
  remote.py   # 远程服务客户端（urllib，MCP 远程模式代理到已运行的 docs-search 服务，含认证）
integrations/ # 非 MCP 协议的 agent 桥接（pi extension）
scripts/      # 瘦包装直跑入口（优先用已安装包，回退 src/）
tests/        # pytest：单元 + CLI/MCP E2E（子进程）+ Web API（内存 HTTP）
```

**分层边界**: 后端可独立部署为远端服务（`docs-search-web --host 0.0.0.0 --token ...`）；
agent 远程模式（`docs-search-mcp --url`）只通过 HTTP 与后端交互，不读本地目录——
两端可分离部署（如后端在服务器、agent 在各开发机）。本地模式仍是单进程直连。

## 常用命令

```bash
pip install -e .                    # 可编辑安装
python scripts/verify.py            # 验证链全链（lint + 全部测试）
python -m pytest tests/test_web.py  # 只跑 Web API（不含 lint）
ruff check src/ tests/ scripts/     # lint
ruff format src/ tests/ scripts/    # 格式化
python scripts/docs-search.py search "kw" --dir <目录>   # 直跑 CLI
python scripts/docs-search-web.py <目录> --no-browser    # 直跑 Web
python scripts/docs-search-mcp.py <目录>                 # 直跑 MCP stdio server
python -m pytest tests/test_mcp.py                      # 只跑 MCP E2E
```

## 路径解析契约（不绑定本地路径）

- 文档目录：`--dir` > `$DOCS_SEARCH_DIR` > `./docs`
- 索引库：`--db` > `$DOCS_SEARCH_DB` > `~/.docs-search/<目录哈希>/index.db`
- 远程服务（MCP 远程模式）：`--url` > `$DOCS_SEARCH_URL`；未配置则为本地模式
- **禁止**在源码中出现任何个人/本机绝对路径（测试 `test_no_hardcoded_paths` 会拦截）

## 设计约束

- **零运行时依赖**：`src/` 只 import 标准库；开发工具（pytest/ruff）只进 `[dependency-groups]`
- **索引库隔离**：库路径 = 目录哈希，多文档库并存互不污染；重建必须幂等（`DROP TABLE IF EXISTS`）
- **Windows 兼容**：控制台输出强制 UTF-8；stdio 协议（MCP）读写走二进制 buffer 显式 UTF-8，不依赖控制台代码页；HTTPServer 禁用 `SO_REUSEADDR` 防多进程重复绑定
- **上传安全**：`.md` only、≤10MB、文件名消毒、删除仅限 `uploads/`——安全测试用例不得删减
- **远端认证**：后端 `--host` 非回环时**必须**启用认证（`--token` Bearer 或 `--user/--password` Basic，
  二选一），否则拒绝启动；凭据比较用 `hmac.compare_digest`；认证配置改动需同步补测试
- **SQL 注入**：所有 SQL 一律参数化查询（`?` 占位符），用户输入不参与结构拼接——新查询必须沿用该模式

## 发布

维护者流程见 [PUBLISHING.md](PUBLISHING.md)。
