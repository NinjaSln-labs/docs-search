# docs-search MCP / Agent 接入指南

> 把本地文档库接到 AI agent 的两条路径:
> **① MCP stdio server**(零依赖,纯 Python 标准库实现)——覆盖 Cursor / ZCode / Qoder / DSH 等一切支持 MCP 的 agent;
> **② pi extension**——pi 无内置 MCP,提供 TS 扩展桥接到同一个 server。
> 工具逻辑只有一份(Python),所有 agent 共享同一套检索行为。

## 前置条件

```bash
pip install docs-search        # 获得 docs-search-mcp 命令(推荐)
# 或仓库直跑: python scripts/docs-search-mcp.py --dir <文档目录>
docs-search-mcp --dir ./docs   # 手工自检: 无输出挂起即正常(stdio 等待客户端),Ctrl+C 退出
```

环境变量: `DOCS_SEARCH_DIR` 默认文档目录;路径规则与 CLI 一致(`--dir` > 环境变量 > `./docs`)。

## 远程模式（连接已运行的服务）

默认本地模式: MCP server 自己读文档目录、建索引。若**已有 docs-search 服务在运行**
（本机 `docs-search-web <DIR>`，或其他机器上启动的、接口与 `/api/*` 相同的服务），
可以让 MCP 直接连它——不读本地目录、不起本地索引，5 个工具全部代理到该服务的 HTTP 接口：

```bash
docs-search-mcp --url http://192.168.1.10:8765
# 或环境变量(--url 优先):
DOCS_SEARCH_URL=http://192.168.1.10:8765 docs-search-mcp
```

- 工具输出格式与本地模式一致（各 agent 行为不变），后端改为 HTTP 调用服务的 `/api/*`
  （`docs_search`→search / `docs_read`→show / `docs_write`→upload / `docs_delete`→delete / `docs_info`→list+stats）
- 地址可写裸 `ip:port`（自动补 `http://`）；`--url` > `$DOCS_SEARCH_URL`，未配置则为本地模式
- 启动时探活一次（`/api/stats`）：连不上立刻报错退出（MCP 客户端会展示并自动重启重试），不挂起无输出
- 适用场景：文档库在另一台机器/容器、服务已由他人启动、多 agent 共享同一服务
- **安全由服务端强制执行**（上传 `.md` only/消毒、删除仅限 `uploads/`）——只连可信服务，配地址前确认网络可达与访问边界

Cursor 远程模式示例：

```json
{
  "mcpServers": {
    "docs-search": {
      "command": "docs-search-mcp",
      "args": ["--url", "http://192.168.1.10:8765"]
    }
  }
}
```

---

## 暴露的 MCP 工具(5 个)

| 工具 | 参数 | 说明 |
|---|---|---|
| `docs_search` | `query`(必填,空格分隔多关键词 AND)、`limit`、`cat` | 检索,返回路径 + 150 字摘要;全文跟 `docs_read` |
| `docs_read` | `path`(必填,相对路径) | 读全文 |
| `docs_write` | `filename`(仅 `.md`)、`content` | 写入 `uploads/` 并即刻索引;同名自动 `-1/-2`;**勿写密钥/隐私** |
| `docs_delete` | `path`(必须 `uploads/` 下) | 删除已写入文档;库内文档拒绝(设计如此) |
| `docs_info` | `mode`(`list`/`stats`)、`cat` | 枚举文档 / 统计与分类 |

本地模式索引在每次调用前自动增量重建,无需手动维护;多关键词是精确子串 AND 匹配(含中文)。
远程模式不建索引,由已运行的服务端维护。

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
(`--url` 启动 MCP server,不读本地目录),适合服务已启动/异机共享文档库的场景。
要求已 `pip install docs-search`;或设 `DOCS_SEARCH_MCP_CMD="python <仓库>/scripts/docs-search-mcp.py"` 指定启动命令。
改完 `/reload` 热加载;工具名:`docs_search` / `docs_read` / `docs_write` / `docs_delete` / `docs_info`。

---

## 典型用法(Agent 视角)

1. **检索**: `docs_search query="MCP 配置"` → 命中路径 → `docs_read path=...` 取全文
2. **沉淀记忆**: 会话结束把结论写入 → `docs_write filename="session-2026-09-07-x.md" content="# 结论 ..."` → 下个会话可搜
3. **库概况**: `docs_info mode="stats"` 看总数/分类;`docs_info mode="list"` 枚举路径

## 安全注意

- 本地模式: server 只操作 `--dir` 指定目录;写入仅限 `uploads/` 且文件名消毒(路径穿越降级为 basename);无鉴权、无网络(纯 stdio);文档库对本机进程可读——**不要写入密钥/隐私原始数据,先脱敏**
- 远程模式: 仅向 `--url`/`$DOCS_SEARCH_URL` 指定的服务发请求,上传/删除防护由服务端强制执行——**只连可信服务**
- 漏洞勿公开披露:走 [SECURITY.md](SECURITY.md) 的私密报告渠道

## 反馈

- 协议细节以源码为准:[src/docs_search/mcp.py](../src/docs_search/mcp.py)(JSON-RPC 2.0 over stdio,按行分隔)
- 工具缺陷/建议:<https://github.com/NinjaSln-labs/docs-search/issues>
