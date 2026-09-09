# docs-search MCP / Agent 接入指南

> 把本地文档库接到 AI agent 的两条路径:
> **① MCP stdio server**(零依赖,纯 Python 标准库实现)——覆盖 Cursor / ZCode / Qoder / DSH / Cline / OpenCode / Reasonix / Command Code 等一切支持 MCP 的 agent;
> **② pi extension**——pi 无内置 MCP,提供 TS 扩展桥接到同一个 server。
> 工具逻辑只有一份(Python),所有 agent 共享同一套检索行为。

## 前置条件

```bash
pip install docs-search        # 获得 docs-search-mcp 命令(推荐)
# 或仓库直跑: python scripts/docs-search-mcp.py --dir <文档目录>
docs-search-mcp --dir ./docs   # 手工自检: 无输出挂起即正常(stdio 等待客户端),Ctrl+C 退出
```

环境变量: `DOCS_SEARCH_DIR` 默认文档目录;路径规则与 CLI 一致(`--dir` > 环境变量 > `./docs`)。
工作空间为**操作级**: 工具调用时传 `workspace` 参数即切到自包含库(不填=默认库),无需启动配置。

> 💡 **一键安装**: 已安装的 agent 想自动写入配置（含 `--agents` 子集 / `--mcp-arg` 附加参数），
> 用 `docs-search-install`（见 [HUMAN-GUIDE.md](HUMAN-GUIDE.md)「一键安装到 AI agent」节）——
> 本页下面的配置就是它写入的目标形态。

## 远程模式（连接已运行的服务）

默认本地模式: MCP server 自己读文档目录、建索引。若**已有 docs-search 服务在运行**
（本机 `docs-search-web <DIR>`，或其他机器上启动的、接口与 `/api/*` 相同的服务），
可以让 MCP 直接连它——不读本地目录、不起本地索引，5 个工具全部代理到该服务的 HTTP 接口：

```bash
docs-search-mcp --url http://192.168.1.10:8765
# 或环境变量(--url 优先):
DOCS_SEARCH_URL=http://192.168.1.10:8765 docs-search-mcp
# 连接代理: 默认跟随环境/系统代理(http_proxy 等);干扰时强制直连或显式指定
docs-search-mcp --url http://192.168.1.10:8765 --proxy direct          # 忽略一切环境代理,直连
DOCS_SEARCH_PROXY=http://proxy.corp:8080 docs-search-mcp --url http://192.168.1.10:8765  # 走指定代理
```

- 工具输出格式与本地模式一致（各 agent 行为不变），后端改为 HTTP 调用服务的 `/api/*`
  （`docs_search`→search / `docs_read`→show / `docs_write`→upload / `docs_delete`→delete / `docs_info`→list+stats）
- 地址可写裸 `ip:port`（自动补 `http://`）；`--url` > `$DOCS_SEARCH_URL`，未配置则为本地模式
- **认证**（远端服务启用了认证时，二选一）：`--token`（Bearer）或 `--user/--password`（Basic）；
  环境变量回退 `$DOCS_SEARCH_TOKEN` / `$DOCS_SEARCH_USER` / `$DOCS_SEARCH_PASSWORD`；
  **显式认证参数优先**：配置里显式给了任一认证参数（`--token` 或 `--user/--password`），env 认证回退
  整体关闭（防本机 env 残留另一路凭据触发“互斥”/串凭据——agent 配置显式 `--token` 时
  `$DOCS_SEARCH_USER`/`$DOCS_SEARCH_PASSWORD` 常驻不再影响）；
  凭据缺失/错误时探活直接失败并报错退出（MCP 客户端会展示），不静默挂起
- **连接代理**（连接层，默认跟随环境/系统代理 `http_proxy`/`all_proxy` 等，`no_proxy` 中的主机仍直连）：
  `--proxy http://proxy.corp:8080` 显式指定 HTTP 代理（裸 `host:port` 自动补 `http://`，支持
  `user:pass@` 代理认证；显式配置不被 no_proxy/注册表排除规则旁路）；`--proxy direct`（或 `none`/`off`）
  强制直连，忽略一切环境代理——环境代理为 socks5（标准库 urllib 不支持 socks，会显式报错）、
  或代理无法转发内网地址时用它；环境变量 `DOCS_SEARCH_PROXY` 同义（`--proxy` 优先）；
  探活失败时若检测到环境代理，错误提示会建议 `--proxy direct`
- 启动时探活一次（`/api/stats`）：连不上立刻报错退出（MCP 客户端会展示并自动重启重试），不挂起无输出
- 适用场景：文档库在另一台机器/容器、服务已由他人启动、多 agent 共享同一服务
- **安全由服务端强制执行**（上传 `.md` only/消毒、删除仅限 `uploads/`；服务端可启用 Bearer/Basic 认证）——只连可信服务，配地址前确认网络可达与访问边界
- **自定义服务**：想自己实现一个兼容服务（任何语言/框架），完整接口规范见 **[SERVER-CONTRACT.md](SERVER-CONTRACT.md)**（服务端视角契约 + 验收自检）

Cursor 远程模式示例：

```json
{
  "mcpServers": {
    "docs-search": {
      "command": "docs-search-mcp",
      "args": ["--url", "http://192.168.1.10:8765", "--token", "<TOKEN>"]
    }
  }
}
```

远端服务启用了认证时（推荐部署方式）：服务端 `docs-search-web <DIR> --host 0.0.0.0 --token <TOKEN>`
（Bearer）或 `--user/--password`（Basic）；agent 侧对应带 `--token` / `--user+--password`，或设同名环境变量。

---

## 暴露的 MCP 工具(5 个)

| 工具 | 参数 | 说明 |
|---|---|---|
| `docs_search` | `query`(必填,空格分隔多关键词 AND)、`limit`、`cat`、`workspace` | 检索,返回路径 + 150 字摘要;全文跟 `docs_read`;`workspace="all"` 跨库聚合(结果带来源) |
| `docs_read` | `path`(必填,相对路径)、`workspace` | 读全文（不支持 `all`） |
| `docs_write` | `filename`(仅 `.md`)、`content`、`if_exists`(`error`/`overwrite`/`keep`)、`workspace` | 写入 `uploads/` 并即刻索引;同名默认提示(`error`,不写不覆盖),`overwrite` 强制覆盖,`keep` 生成 `-1/-2` 新文件;不支持 `all`;**勿写密钥/隐私** |
| `docs_delete` | `path`(必须 `uploads/` 下)、`workspace` | 删除已写入文档;库内文档拒绝(设计如此);不支持 `all` |
| `docs_info` | `mode`(`list`/`stats`)、`cat`、`workspace` | 枚举文档 / 统计与分类;`workspace="all"` 跨库聚合(带来源/库列表) |

本地模式索引在每次调用前自动增量重建,无需手动维护;多关键词是精确子串 AND 匹配(含中文)。
远程模式不建索引,由已运行的服务端维护。
5 个工具均接受可选 `workspace` 参数（操作级，本地/远程一致）: 指定即切到自包含库
`~/.docs-search/workspaces/<ws>/`，与默认库天然隔离；不传 = 默认库。

---

## Cursor

**已实测通过**(Cursor Agent CLI 2026.09.02,非交互 `--yolo` 模式;IDE Agent 同一配置)。

全局:`%USERPROFILE%\.cursor\mcp.json`(Windows)或 `~/.cursor/mcp.json`;项目级:`<项目>\.cursor\mcp.json`。

```json
{
  "mcpServers": {
    "docs-search": {
      "command": "docs-search-mcp",
      "args": ["--dir", "D:/path/to/your/docs"],
      "env": {}
    }
  }
}
```

- 命令找不到时改绝对路径(如 `C:/Python312/Scripts/docs-search-mcp.exe` 或仓库直跑 `python` + `scripts/docs-search-mcp.py`)
- CLI 实测要点：`agent -p` 非交互模式下 MCP 调用默认需审批，加 `--trust`（工作区信任）+ `--yolo`（自动批准）
- 另有独立 CLI（`cursor.com/install`，命令 `agent`，与 IDE 凭据独立，需 `agent login`）

## ZCode

**agent 级已实测通过**（Mac, ZCode CLI 0.16.5，`zcode.cjs -p` 非交互直调 docs_search：
搜「上传」3 命中）。关键要点：

- **CLI 入口**：`node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`（非 PATH 命令；
  支持 `-p/--prompt` 非交互）
- **模型与 MCP 同文件**：`~/.zcode/cli/config.json`（GUI 用 `~/.zcode/v2/config.json`，两者独立）
- **第三方供应商**：`provider.<id>` 条目，`kind: "openai-compatible"`（或 `"anthropic"`）+
  `options.apiKey/baseURL`；实测用 pi 的 tokenrouter 挂 `z-ai/glm-5.3-free` 直连成功
- **model 角色**：`model.main` 是**字符串** `"<providerId>/<modelId>"`（不要写成对象，
  `.strict()` schema 会整段拒收）；注意 `apiKey: ""`（空串）也会导致整个 config 被判 invalid 丢弃
- **MCP**：同一文件的 `mcp.servers` 键（注意不是 `mcpServers`）：

```json
{
  "model": { "main": "tokenrouter/z-ai/glm-5.3-free" },
  "provider": {
    "tokenrouter": {
      "kind": "openai-compatible",
      "options": { "apiKey": "...", "baseURL": "https://api.tokenrouter.com/v1" },
      "enabled": true,
      "models": {
        "z-ai/glm-5.3-free": {
          "limit": { "context": 262144, "output": 8192 },
          "modalities": { "input": ["text"], "output": ["text"] }
        }
      }
    }
  },
  "mcp": {
    "servers": {
      "docs-search": {
        "command": "docs-search-mcp",
        "args": ["--dir", "/path/to/your/docs"],
        "enable": true
      }
    }
  }
}
```

GUI 侧（Settings → MCP Servers）同样支持表单和 Full configuration mode（兼容 `mcpServers` 结构），
配置落盘后 CLI 与 GUI 独立读取；调试看 `~/.zcode/cli/log/zcode-<date>.jsonl`
（`config.file.invalid` 事件会给出精确的 schema 报错）。

## Qoder

**agent 级已实测通过**（Mac, qodercn CLI 1.1.44,`-p` 非交互直调 docs_search:
搜「上传」3 命中)。注意:

- **额度在 Qoder CN 版**(`qodercn` / Qoder CN.app,npm 包 `@qodercn-ai/qodercli`),
  国际版 `qoder` 是独立账号体系——两边订阅不通用,选对 CLI 再配

```bash
qodercn mcp add -s user docs-search -- docs-search-mcp --dir /path/to/your/docs
qodercn mcp list        # 应显示 docs-search ✓ Connected
```

- `-s user` 写入用户级配置,全局可用(home 目录下不加会被拒)
- 命令找不到时改绝对路径(pip 装在 `~/.local/bin` 时注意 PATH)
- CLI 已在运行时用 `/mcp reload` 重新发现;新会话自动加载
- agent 非交互:`qodercn -p [--dangerously-skip-permissions] "…"`

## DSH(DeepSeek Harness)

DSH 内置 MCP 客户端插件 `@deepseek-ai/dsh-mcp-client`(每服务器一条配置,工具以
`mcp__docs-search__docs_search` 形式出现)。**已实测通过**(dsh 0.1.2-alpha.4,headless profile)。

```bash
# ① 把插件装入目标 profile(转发 pnpm,一次性)
dsh plugin --profile headless add @deepseek-ai/dsh-mcp-client

# ② 在 ~/.dsh/profiles/<name>/cordis.patch.yml 加 insert 行(新增条目必须包 insert:)
```

```yaml
- insert:
    - id: mcp-docs-search
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: docs-search
        transport: stdio
        command: docs-search-mcp
        args: ['--dir', '/path/to/your/docs']
```

一次性测试(不动 profile,overlay 同样要求 insert 形状):

```bash
dsh --profile headless --patch /tmp/ds.cordis.yml '调用 mcp__docs-search__docs_search 搜索……'
```

注意:
- 插件 add 后仅进 node_modules,`--dump-config` 能看到 insert 行才算激活
- 支持字段:`toolCallTimeoutMs`(默认 60s)、`failOnStartupError`、`reconnect.*`,详见上游 config-catalog
- 参考上游指南:<https://deepseek-harness.github.io/deepseek-harness/>(packages/mcp + user guide mcp-memory)

## Cline(CLI)

**agent 级已实测通过**（Cline CLI 3.0.61，非交互直调 docs_search 搜「远程模式」1 命中；
`cline config mcp` 识别 docs-search [stdio]）。

- **配置路径**：`~/.cline/data/settings/cline_mcp_settings.json`（CLI 实际读取路径；官方文档早先写的
  `~/.cline/mcp.json` 是错的，见 cline#11671）；IDE 侧同文件或 `%USERPROFILE%\.cline\cline_mcp_settings.json`
- **格式**：标准 `mcpServers`（与 Cursor 同构）

```json
{
  "mcpServers": {
    "docs-search": {
      "command": "docs-search-mcp",
      "args": ["--url", "https://example.com:8765"],
      "env": {}
    }
  }
}
```

- 本地模式把 `args` 改为 `["--dir", "D:/path/to/your/docs"]`；命令找不到时改绝对路径
- CLI 用法：`cline "检索 docs 库中 MCP 配置"`（act 模式默认 auto-approve）；`cline -p` 为 plan 模式；
  `--json` 结构化输出；验证服务器用 `cline config mcp`（**注意 `cline mcp list` 不存在**——
  `cline mcp install|uninstall` 是需 TTY 的向导，非交互场景用 `config mcp`）

## OpenCode(V2)

**agent 级已实测通过**（opencode 1.18.30，`opencode run` 直调 docs_search 搜「MCP」8 命中；
`opencode mcp list` 显示 connected）。

- **配置路径**：全局 `~/.config/opencode/opencode.jsonc`（项目级放仓库根 `opencode.jsonc`，优先级更高）
- **格式**：V2 把服务器放 `mcp.servers` 下（**不是**直接把名字放 `mcp` 下）；`type: "local"` = stdio
  启动命令，`command` 是数组（可执行文件 + 参数）；无 `enabled` 字段（默认连接，`disabled: true` 才关）

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "docs-search": {
        "type": "local",
        "command": ["docs-search-mcp", "--url", "https://example.com:8765"],
        "environment": {}
      }
    }
  }
}
```

- 本地模式把 `command` 改为 `["docs-search-mcp", "--dir", "D:/path/to/your/docs"]`
- 远端服务启用认证时凭据放 `environment`（如 `"environment": { "DOCS_SEARCH_TOKEN": "{env:DOCS_SEARCH_TOKEN}" }`）
  或依赖进程环境变量回退；`environment` 只增不改（继承的冲突变量无法删除）
- 验证：`opencode mcp list` 看 connected 状态

## Reasonix

**agent 级已实测通过**（reasonix v1.38.3，`-p` 非交互直调 docs_search 搜「MCP 配置」8 命中；
添加时返回 “ready with 5 tools”）。

- 自带 MCP 管理子命令，无需手写配置（全局条目写入 `reasonix.toml` 或
  Windows `%APPDATA%\reasonix\config.toml`；项目条目写仓库根 `reasonix.toml`）：

```bash
# stdio（argv 无 shell，命令与参数分开）
reasonix mcp add docs-search -- docs-search-mcp --dir D:/path/to/your/docs
# 远程模式（连已运行/自定义服务）
reasonix mcp add docs-search -- docs-search-mcp --url https://example.com:8765
# 验证 / 管理
reasonix mcp list        # 应显示 docs-search (stdio)
reasonix mcp remove docs-search
```

- 认证凭据不在命令里写（机密），靠环境变量回退 `$DOCS_SEARCH_TOKEN` 等——与 Cline/OpenCode 的
  `env` 字段不同，reasonix 的 stdio argv 形式**无法注入环境变量**，凭据一律走进程环境
- 非交互调用：`reasonix -p [--permission-mode MODE] "…"`；`reasonix run "…"` 分步执行

## Command Code

**MCP 服务器配置/连接已验证**（Command Code v1.51.3，`mcp list` 显示 docs-search stdio enabled；
harness 的 system prompt 注入 `mcp__docs-search__docs_search` 工具描述——模型可感知工具存在）。

- **CLI 名**：官方文档写作 `cmd`（npm 包 command-code 注册了 `cmd` / `cmdc` / `command-code` /
  `commandcode` 四个别名）——**Windows 下 `cmd` 撞系统 cmd.exe，请用 `commandcode` 或 `cmdc`**
- **配置**：自带 MCP 管理子命令（stdio argv 用 `--` 分隔，选项放服务器名前）：

```bash
# stdio（本地模式）
commandcode mcp add --transport stdio docs-search -- docs-search-mcp --dir D:/path/to/your/docs
# 远程模式（连已运行/自定义服务；认证走环境变量回退，见上）
commandcode mcp add --transport stdio docs-search -- docs-search-mcp --url https://example.com:8765
# 验证 / 管理
commandcode mcp list           # 应显示 docs-search stdio local enabled
commandcode mcp remove docs-search
```

- 工具命名：`mcp__docs-search__docs_search`（会话内 `/mcp` 菜单看连接状态与工具数）
- **headless `-p` 实测限制**（Command Code harness 侧行为，非本 server 问题）：`-p` 非交互 + BYOK
  模型时，发给模型的请求 tools 参数**不含 MCP 工具**（仅内置工具；harness 异步加载 stdio MCP
  晚于工具快照，与 Claude Code #74926 同型）——模型只能“看到”工具描述而无法实际调用；
  `--tools-all` / `--tools-enable` 均不解决（工具未被 withhold，而是未进入快照）。
  **建议交互模式使用**（`commandcode` 进 TTY 会话，/mcp 确认连接后工具随会话可用）；
  待 Command Code 修复 harness 快照时序后 headless 即可直调
- **BYOK 参考**（headless 需 `COMMAND_CODE_API_KEY` 过认证检查）：`~/.commandcode/providers.json`
  （`providers.<id>` 对象：`api`（openai-completions / anthropic-messages）、`baseURL`、
  `apiKey`（`$ENV_VAR` 或 `{env:VAR}` 引用，勿写裸密钥）、`models` **对象 map** 键为模型 id）；
  实测 GLM-5.3 经 tokenrouter 的 anthropic wire 工具调用正常（API 层验证 tool_use 正确返回）

## pi(@earendil-works/pi-coding-agent)

**已实测通过**（`pi -e integrations/pi/docs-search.ts`，docs_search/docs_info 双工具验证）。
pi 无内置 MCP，用本仓库提供的扩展（位于 [integrations/pi/docs-search.ts](../integrations/pi/docs-search.ts)），
它以子进程方式桥接同一个 MCP server——工具实现与 MCP 客户端完全一致：

```bash
# 全局(所有项目)
cp integrations/pi/docs-search.ts ~/.pi/agent/extensions/docs-search.ts
# 或项目级(仅当前项目,首次加载需信任)
cp integrations/pi/docs-search.ts .pi/extensions/docs-search.ts
```

文档目录解析:`DOCS_SEARCH_DIR` 环境变量 > `./docs`(pi 启动目录)。
远程模式: 设 `DOCS_SEARCH_URL`(如 `http://192.168.1.10:8765`)时改连已运行的服务
(`--url` 启动 MCP server,不读本地目录),适合服务已启动/异机共享文档库的场景;
远端启用认证时,凭据用 `DOCS_SEARCH_TOKEN`(Bearer)或 `DOCS_SEARCH_USER`+`DOCS_SEARCH_PASSWORD`(Basic)。
连接代理: `DOCS_SEARCH_PROXY=http://ip:port` 走指定代理,或 `direct`/`none` 强制直连(忽略环境代理);
未设置时跟随系统/环境代理(`http_proxy` 等)。
要求已 `pip install docs-search`;或设 `DOCS_SEARCH_MCP_CMD="python <仓库>/scripts/docs-search-mcp.py"` 指定启动命令。
改完 `/reload` 热加载;工具名:`docs_search` / `docs_read` / `docs_write` / `docs_delete` / `docs_info`。

---

## 典型用法(Agent 视角)

1. **检索**: `docs_search query="MCP 配置"` → 命中路径 → `docs_read path=...` 取全文
2. **沉淀记忆**: 会话结束把结论写入 → `docs_write filename="session-2026-09-07-x.md" content="# 结论 ..."` → 下个会话可搜
3. **库概况**: `docs_info mode="stats"` 看总数/分类;`docs_info mode="list"` 枚举路径

## 安全注意

- 本地模式: server 只操作 `--dir` 指定目录;写入仅限 `uploads/` 且文件名消毒(路径穿越降级为 basename);无网络(纯 stdio);文档库对本机进程可读——**不要写入密钥/隐私原始数据,先脱敏**
- 远程模式: 仅向 `--url`/`$DOCS_SEARCH_URL` 指定的服务发请求(可经 `--proxy` 指定的代理),上传/删除防护与认证(Bearer/Basic)由服务端强制执行——**只连可信服务,凭据不要写进会话/文档,只放配置或环境变量**
- 漏洞勿公开披露:走 [SECURITY.md](SECURITY.md) 的私密报告渠道

## 反馈

- 协议细节以源码为准:[src/docs_search/mcp.py](../src/docs_search/mcp.py)(JSON-RPC 2.0 over stdio,按行分隔)
- 工具缺陷/建议:<https://github.com/NinjaSln-labs/docs-search/issues>
