# Changelog

本文件记录所有对外可见的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本遵循 SemVer。

## [1.2.0] — 2026-09-08

### Added

- **服务端契约文档 [docs/SERVER-CONTRACT.md](docs/SERVER-CONTRACT.md)**：自定义 docs-search 兼容服务的完整接口规范
  （6 端点契约/全局错误约定/Bearer+Basic 认证/workspace 语义/最小可用子集/验收自检 curl），
  任何语言可按契约复刻官方 web.py；MCP 远程模式与 API.md 交叉引用
- **MCP 远程模式**（`docs-search-mcp --url http://ip:port` 或 `$DOCS_SEARCH_URL`）：用户已运行
docs-search 服务（本机 `docs-search-web` 或其他机器上接口相同的服务）时，不读本地目录、不起本地索引，
5 个工具全部代理到该服务的 `/api/*`（输出格式与本地模式一致）；地址支持裸 `ip:port`；启动探活
（连不上立即报错退出，不挂起）；新增 `src/docs_search/remote.py`（纯标准库 urllib 客户端，含
连接层短重试）；pi 扩展支持 `DOCS_SEARCH_URL` 直连；对接文档 docs/MCP.md 补远程模式节；
tests 新增 remote 客户端单测 + MCP 远程 E2E + web search+cat 回归（46→62 用例）
- **远端认证（Bearer/Basic）**：后端 `docs-search-web --token <T>`（Bearer）或 `--user/--password`（Basic）
  二选一启用认证，保护全部路径（含 Web UI），凭据比较用 `hmac.compare_digest`；**监听非回环地址
  时必须启用认证否则拒绝启动**（防服务裸奔公网）；远程模式侧 `docs-search-mcp --token` /
  `--user+--password`（环境变量 `DOCS_SEARCH_TOKEN` / `DOCS_SEARCH_USER` / `DOCS_SEARCH_PASSWORD` 回退）
  携带凭据，探活时凭据错误立即报错退出；pi 扩展透传同名环境变量；文档：docs/API.md 认证节、
  MCP.md 远程认证、DEVELOPMENT 分层边界（后端可独立部署为远端，agent 远程模式纯 HTTP 代理）；
  tests 新增认证用例 + SQL 注入安全证明（62→75 用例）
- **工作空间（Workspace，操作级）**：MCP 5 工具 `workspace` 参数 / Web API `?ws=` / CLI `--workspace`
  每次操作动态切库，不在服务启动时绑定；指定 → 自包含库 `~/.docs-search/workspaces/<ws>/`
  （docs + index.db，天然隔离），不填 = 默认库；本地/远程模式一致（remote 经 `ws=` query 透传）；
  tests 新增 API/MCP/remote 三侧 workspace 隔离与透传用例
- **上传同名策略 `if_exists`**（Web `?if_exists=`、MCP `docs_write`、CLI `--if-exists`）：默认
  `error`（重名返回提示，不写不覆盖）> `overwrite`（强制覆盖原文件）> `keep`（生成 `-1/-2` 新文件，
  即旧默认行为）；三入口语义一致，`core.resolve_upload_target` 单点实现；tests 覆盖三策略 + 未知策略拒绝

### Changed

- **上传同名默认行为变更（破坏性）**：同名上传从自动 `-1/-2` 去重改为**默认返回重名提示**（不写不覆盖）；
  需要旧行为时显式传 `if_exists=keep`，覆盖用 `if_exists=overwrite`

### Fixed

- **Web `/api/search` 带 `cat` 参数时 SQL 语法错误**（`AND cat = ?` 被拼在 `LIMIT 20` 之后）——
  修复并补回归测试（影响 Web UI 分类搜索与 MCP 远程模式的 cat 过滤）

### Security

- **SQL 注入防护确认**：所有查询均为参数化查询（`?` 占位符 + 参数绑定），`q`/`cat`/`path` 等用户输入
  按字面值处理、不参与 SQL 结构拼接——无注入面；补安全测试证明（`' OR '1'='1`、`DROP TABLE`、
  `UNION` 等载荷不改变查询结构、库不受损），文档见 docs/API.md「安全（SQL 注入）」节

## [1.1.0] — 2026-09-08

### Added

- **MCP stdio server**（`docs-search-mcp`，`src/docs_search/mcp.py`）：零依赖纯标准库实现
  JSON-RPC 2.0 over stdio，暴露 5 个工具（docs_search / docs_read / docs_write / docs_delete / docs_info），
  一份实现同时对接 Cursor（mcp.json）/ ZCode（MCP Servers 表单或 mcpServers JSON）/ Qoder（`qoder mcp add`）/
  DSH（`@deepseek-ai/dsh-mcp-client` 插件行）等 MCP 客户端；stdio 读写走二进制 buffer 显式 UTF-8（Windows GBK 坑）；
  pi 无内置 MCP，补 `integrations/pi/docs-search.ts` 扩展子进程桥接同一 server（`pi.registerTool` 五工具）；
  对接文档 `docs/MCP.md`（各 agent 配置均按官方文档核实）；tests/test_mcp.py +5 用例（含 CJK/守卫/噪声行/EOF）；
  实机验证：pi（extension）+ dsh 0.1.2-alpha.4（headless profile，insert patch 形状）+ Cursor Agent CLI
  （非交互 --trust --yolo）三方同语料库结果一致（搜「上传」3 命中，首条 AGENT-GUIDE.md）
- 发布链路：TestPyPI 灰度作业（workflow_dispatch）+ tag↔pyproject 版本守卫 + twine check；
  GitHub Environments pypi/testpypi（required reviewers + `v*` tag 部署策略）
- TestPyPI 灰度发布 1.0.0 成功（whl 18.5KB / sdist 22KB），实机 venv 安装 smoke 通过
- **正式发布 PyPI 1.0.0**：版本守卫 + twine check + pypi 环境人工批准；实机 `pip install docs-search` smoke 通过

### Changed

- upload/download-artifact 显式命名：download@v4 无 name 时按 artifact 建子目录，
  dist/ 实为空导致发布失败（已修复并验证）
- publish.yml 改回最小化权限：顶层 `permissions: {}`，`id-token: write` 仅 publish job 持有
  （上游 repo-audit v1.3.0 SEC-004 支持 job 级 fallback 检测，撤销之前的顶层 workaround）
- PUBLISHING.md 前置配置补齐上游模板细节：environment 必开 required reviewers、
  pending publisher 抢注风险警告
- AGENTS.md 头部修正：scaffold `--update` 可用于存量仓（3a 纠偏），手写自主维护声明保留

### Changed（工程标准化）

- Python 支持地板升至 3.10（pytest 9 要求 ≥3.10；3.8/3.9 已 EOL；零依赖直跑不受影响）
- 验证链收敛单源 `scripts/verify.py`：pre-commit 钩子与 CI 均调用它，不再复制命令
- CI 开发依赖改由 `requirements.lock` 锁定安装
- AGENTS.md 来源声明改为"改编自模板、仓库内维护"（修正失实的"单源拼装"声明，上游反馈 NinjaSln-labs/repo-audit#3）

## [1.0.0] - 2026-09-06

### Added（首个开源版本）

- 零依赖本地文档搜索引擎：SQLite 索引、多关键词 AND 检索、自动增量重建
- 路径解析不绑定本地路径：`--dir` / `DOCS_SEARCH_DIR` / `./docs`；索引库按目录哈希隔离（`~/.docs-search/<hash>/`）
- Web UI + 上传/删除 API（`.md` only、≤10MB、文件名消毒、删除仅限 `uploads/`）
- CLI：index / search / list / show / status / upload / open（跨平台）
- 包化发布：`pip install` 提供 `docs-search` / `docs-search-web` 命令；`scripts/` 直跑瘦包装
- 工程标准化：pytest 测试套件（单元 + CLI E2E + Web API）、`scripts/verify.py` 验证链、CI（SHA 固定、ubuntu/windows 矩阵）、PyPI 发布工作流（Trusted Publishing）、AGENTS/CONTRIBUTING/DEVELOPMENT/SECURITY/PUBLISHING 文档
- 通过 repo-audit 标准化审计：python-app 25/25 规则，100/100 (A)
