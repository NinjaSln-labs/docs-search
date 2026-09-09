# docs-search-install — 一键安装器

把 docs-search 的 MCP/扩展配置自动写入**已安装的 AI agent**——不用逐家手改配置文件。
检测到谁装谁，幂等可重跑，覆盖前自动备份。

```bash
# 本地模式（文档目录在本机；默认 ./docs）
docs-search-install --dir D:/path/to/your/docs
# 远程模式（连已运行/自定义服务；认证走环境变量回退）
docs-search-install --url https://docs.example.com
```

> 命令来源：`pip install docs-search`（v1.3.1+）或仓库直跑 `python scripts/docs-search-install.py`。
> 各 agent 配置格式的权威说明见 [MCP.md](MCP.md)（九家配置节）——本页是安装器的操作手册。

## 支持的 agent（九家）

| agent | 检测依据 | 写入方式 | 配置位置 |
|---|---|---|---|
| pi | `~/.pi/agent/extensions/` | 扩展文件 copy（仓库 `integrations/pi/docs-search.ts`） | `~/.pi/agent/extensions/docs-search.ts` |
| cursor | `~/.cursor/` | JSON 合并 | `~/.cursor/mcp.json` |
| cline | `~/.cline/data/settings/` | JSON 合并 | `~/.cline/data/settings/cline_mcp_settings.json` |
| opencode | `~/.config/opencode/` | JSON 合并（V2 `mcp.servers`） | `~/.config/opencode/opencode.jsonc` |
| commandcode | `~/.commandcode/` | JSON 合并 | `~/.commandcode/mcp.json` |
| zcode | `~/.zcode/cli/` | JSON 合并（`mcp.servers` 键） | `~/.zcode/cli/config.json` |
| reasonix | `reasonix` CLI | 官方 CLI `reasonix mcp add` | 全局 `config.toml` |
| qoder | `qodercn`/`qoder` CLI | 官方 CLI `qodercn mcp add -s user` | qodercn 管理 |
| dsh | `dsh` CLI 或 `~/.dsh/profiles/` | 插件 `dsh plugin add` + patch 行 | `<profile>/cordis.patch.yml` |

未检测到安装的 agent 自动跳过；`--agents` 可指定子集。

## 参数

| 参数 | 说明 |
|---|---|
| `--dir PATH` | 本地模式：文档根目录（默认 `./docs`） |
| `--url URL` | 远程模式：已运行 docs-search 服务地址（`--dir`/`--url` 二选一） |
| `--agents LIST` | 子集，逗号分隔（默认全部检测到的） |
| `--mcp-arg KEY=VALUE` | 追加 docs-search-mcp 启动参数（可重复），如 `--mcp-arg proxy=direct` |
| `--list` | 只列出检测到的 agent 与配置状态，不写入 |
| `--dry-run` | 预览将要执行的写入，不落盘 |
| `--force` | 覆盖已存在的 docs-search 条目（默认跳过；覆盖前备份 `.bak`） |

## 模式

- **本地模式**：写入 `docs-search-mcp --dir <目录>`——agent 启动 server 自己读文档目录、建索引（零网络）。
- **远程模式**：写入 `docs-search-mcp --url <地址>`——agent 连接已运行的 docs-search 服务（本机或其他机器，
  接口与 `/api/*` 相同），不读本地目录。**认证不写在配置里**，走环境变量回退
  `DOCS_SEARCH_TOKEN`（Bearer）或 `DOCS_SEARCH_USER`/`DOCS_SEARCH_PASSWORD`（Basic）；
  连接代理 `DOCS_SEARCH_PROXY`（如 `direct` 直连）。细节见 [MCP.md](MCP.md) 远程模式节。

## 幂等与备份

- 目标条目已存在 → 跳过并提示（不重复加、不改内容）
- `--force` → 覆盖；覆盖前把原文件复制为 `<文件名>.bak`（已存在 `.bak` 不再覆盖，保留首次备份）
- JSON 解析失败（如 opencode.jsonc 含注释的损坏文件）→ 跳过该家并保留原文件，不破坏

## 安全注意

- **认证参数不要写进配置**：`--mcp-arg token=xxx` 会明文落盘——用环境变量回退
  （`DOCS_SEARCH_TOKEN` 等），凭据只放进程环境/密钥管理
- pi 扩展与 MCP server 行为一致，写入 `uploads/` 的内容对文档库进程可读——**勿写密钥/隐私原始数据**
- 只连可信的远程服务；凭据缺失/错误时探活直接失败退出，不静默挂起

## 示例

```bash
# 只装某几家（本地模式）
docs-search-install --dir ./docs --agents pi,cursor,opencode

# 远程模式 + 连接代理 + 只预览
docs-search-install --url https://docs.example.com --mcp-arg proxy=direct --dry-run

# 覆盖已有条目（自动备份 .bak）
docs-search-install --dir ./docs --force

# 查看检测结果（不写入）
docs-search-install --list
```

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `跳过：未检测到` | 该 agent 未安装或其配置目录不存在——先装 agent 再重跑 |
| `跳过：JSON 解析失败` | 配置文件含注释/损坏（如 opencode.jsonc）——安装器避免破坏，按 [MCP.md](MCP.md) 手动配置 |
| `CLI 失败（exit ...）` | reasonix/qoder 官方 CLI 报错（如 MCP server 启动校验失败）——看 stderr 提示 |
| Windows 下 `reasonix` 等找不到 | npm shim（.cmd）需经 `cmd /c` 执行——安装器已内置处理（`_run()`） |
| 安装后 agent 无工具 | 重启 agent 会话/`/reload`；Command Code headless `-p` 的 harness 限制见 [MCP.md](MCP.md) |

## 相关

- 各 agent 手动配置与格式：[MCP.md](MCP.md)
- 工程结构（agent_install.py）：[DEVELOPMENT.md](DEVELOPMENT.md)
- 用户手册（CLI/Web 用法）：[HUMAN-GUIDE.md](HUMAN-GUIDE.md)
