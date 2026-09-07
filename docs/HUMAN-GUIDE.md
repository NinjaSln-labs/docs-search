# docs-search 人类使用手册

> 零依赖的本地文档搜索引擎：给你的 Markdown 文档库一个毫秒级的本地搜索，带 Web 界面和上传功能。
> 无需联网、无遥测、不装任何第三方包（Python 3.10+ 标准库就够）。

---

## 一、这是什么

把一个装满 `.md` 文档的目录变成可搜索的知识库：

- **快**：SQLite 索引，检索 < 50ms；搜索前自动检测文件变更、增量重建，你永远搜到最新内容
- **全本地**：不联网、无遥测、索引存在你自己机器上
- **两种用法**：命令行（脚本/日常）+ 浏览器 Web 界面（可视化搜索、拖拽上传）
- **多库并存**：A 项目文档和 B 项目文档各自独立索引，互不干扰

适合：个人文档库、Agent 记忆库、项目笔记、调研快照集……任何 `.md` 集合。

---

## 二、安装

### 方式 A：pip 安装（推荐）

```bash
pip install docs-search
```

装完得到两个命令：

| 命令 | 用途 |
|------|------|
| `docs-search` | 命令行：索引 / 搜索 / 上传等 |
| `docs-search-web` | 启动 Web 界面（含上传接口的 HTTP 服务） |
| `docs-search-mcp` | MCP stdio server（AI agent 对接，见 [MCP.md](MCP.md)） |

### 方式 B：零安装直跑

克隆仓库后直接跑（连 pip install 都不用）：

```bash
python scripts/docs-search.py --help
python scripts/docs-search-web.py --help
```

要求：Python ≥ 3.10。仅此而已。

---

## 三、30 秒上手

```bash
# 1. 索引一个文档目录（把示例路径换成你自己的）
docs-search index --dir ~/my-notes

# 2. 搜索（多个关键词用空格隔开 = AND 关系）
docs-search search "部署 postgres" --dir ~/my-notes

# 3. 打开浏览器界面
docs-search-web ~/my-notes
# 自动打开 http://127.0.0.1:8765
```

Web 界面：顶部搜索框输入关键词回车；或点分类按钮浏览；`⬆ 上传` 按钮支持点击选择或**把 .md 文件拖进去**。

---

## 四、CLI 完整参考

所有子命令都支持 `--dir`（文档目录）和 `--db`（索引库路径）；不传时的默认值见 [第五节](#五路径规则)。子命令有短别名（括号内）。

### index (`i`) — 重建索引

```bash
docs-search index --dir ~/my-notes
# indexed 135 docs in 46ms
```

全量重建。一般不用手动跑——搜索前会自动检测变更。

### search (`s`) — 搜索

```bash
docs-search search "关键词1 关键词2" --dir ~/my-notes -n 5
# "关键词1 关键词2" -> 3 results (7ms)
```

- 多关键词 = AND（都要命中）
- `-n` 控制条数（默认 8）
- 搜之前自动做增量检测，文档变了会先重建（输出 `[auto] reindexed N docs`）

### list (`l`) — 列出全部

```bash
docs-search list --dir ~/my-notes
```

按分类（= 一级子目录）分组列出，含标题和大小。

### show (`sh`) — 看内容

```bash
docs-search show infra/mcp.md --dir ~/my-notes
```

`path` 是相对文档根的路径（从 `list` 或搜索结果里拿）。显示前 2000 字符。

### status (`st`) — 索引状态

```bash
docs-search status --dir ~/my-notes
# docs:    E:\my-notes
# db:      C:\Users\you\.docs-search\183d87dcaaff\index.db
# records: 135
# updated: 2026-09-07T10:00:00
# stale:   no
```

`stale: yes` 表示文档有变化，下次搜索会自动重建。

### upload (`u`) — 上传文档

```bash
docs-search upload ~/Downloads/new-note.md --dir ~/my-notes
# uploaded: new-note.md -> uploads/new-note.md
# reindexed 136 docs in 45ms
```

复制到文档库的 `uploads/` 子目录并重建索引。同名自动加 `-1`、`-2` 后缀。限制：仅 `.md`、单文件 ≤ 10MB。

### open (`o`) — 打开文档

```bash
docs-search open infra/mcp.md --dir ~/my-notes
```

用系统默认程序（编辑器/浏览器）打开原文件。Windows/macOS/Linux 均支持。

---

## 五、路径规则（重点）

### 文档目录（三选一，优先级从高到低）

| 方式 | 示例 |
|------|------|
| ① `--dir` 参数 | `docs-search search "x" --dir ~/my-notes` |
| ② 环境变量 `DOCS_SEARCH_DIR` | `export DOCS_SEARCH_DIR=~/my-notes` 之后可省略 `--dir` |
| ③ 默认 `./docs` | 当前工作目录下的 `docs/` 子目录 |

### 索引库（自动管理，一般不用管）

默认存放在 `~/.docs-search/<目录哈希>/index.db`。**哈希由文档目录的绝对路径算出**，所以：

- 不同文档目录 → 不同索引库 → 互不干扰、可并存
- 想自定义位置：`--db` 参数或环境变量 `DOCS_SEARCH_DB`

> 迁移/备份：索引可以随时删掉重建（`docs-search index` 即可），真正需要备份的只有你的文档目录本身。

---

## 六、Web 界面与上传接口

### 启动

```bash
docs-search-web ~/my-notes                # 自动开浏览器
docs-search-web ~/my-notes --port 8080    # 换端口
docs-search-web ~/my-notes --no-browser   # 脚本/后台场景
```

### 界面功能

- **搜索**：输入关键词回车，结果含标题、分类、路径、摘要
- **分类浏览**：页面顶部的分类按钮（= 文档根下的一级子目录）
- **上传**：`⬆ 上传` 按钮，点击选择或拖拽 `.md` 文件（支持多选）
- **删除**：`uploads/` 下的文档在结果里有「删除」按钮（库内文档不可删，防误删）

### API 一览（给脚本/Agent 用）

| 方法 | 端点 | 说明 |
|------|------|------|
| GET  | `/api/stats` | 统计信息 |
| GET  | `/api/search?q=关键词&cat=` | 搜索 |
| GET  | `/api/list?cat=` | 列出文档 |
| GET  | `/api/show?path=x.md` | 文档内容 |
| POST | `/api/upload?filename=x.md` | 上传（body = 文件文本内容） |
| POST | `/api/delete?path=uploads/x.md` | 删除 uploads/ 下文档 |

curl 示例：

```bash
curl 'http://127.0.0.1:8765/api/search?q=部署'
curl -X POST 'http://127.0.0.1:8765/api/upload?filename=notes.md' --data-binary @notes.md
```

完整参数与响应结构见 [API.md](API.md)。

---

## 七、安全模型（请阅读）

1. **服务只监听本机**：默认 `127.0.0.1:8765`。接口没有鉴权，**不要**用 `--host 0.0.0.0` 暴露到公网/局域网
2. **上传有防护**：仅 `.md`、≤10MB、文件名消毒（`../evil.md` 会被降级为 `evil.md` 落在 uploads/ 里）、内容必须 UTF-8
3. **删除有边界**：删除接口只能删 `uploads/` 下的文件，文档库本体不可通过接口删除
4. **无遥测**：不联网、不上报、索引数据不出你的机器

发现安全漏洞请勿开公开 Issue，走 [SECURITY.md](https://github.com/NinjaSln-labs/docs-search/blob/main/SECURITY.md) 的私密渠道。

---

## 八、常见问题（FAQ）

**Q：搜索结果为空？**
先 `docs-search status --dir ...` 看 `records` 是否为 0——是则目录里没有 `.md` 文件（检查 `--dir` 是否指对）。非 0 则确实是没匹配，减少关键词再试。

**Q：提示「索引库被占用，无法重建」？**
有另一个 docs-search 进程正在用同一个库（比如 Web 服务开着时又跑了 `index`）。关掉一个再试。

**Q：换了机器/删了索引怎么办？**
无所谓，`docs-search index` 重建即可，索引是纯派生数据。

**Q：能索引 Word/PDF/HTML 吗？**
当前版本只收 `.md`。这是刻意设计——零依赖。转换成 Markdown 后可上传。

**Q：文件名会重复吗？**
上传时同名自动加 `-1`、`-2` 后缀；库内文件路径以相对路径唯一标识。

**Q：Windows 下中文乱码？**
v1.0.0 起控制台输出已强制 UTF-8。若终端仍乱码，执行 `chcp 65001` 后重试。

**Q：Agent/脚本怎么集成？**
优先走 HTTP API（`docs-search-web --no-browser` 起服务），机器可读协议见 [AGENT-GUIDE.md](AGENT-GUIDE.md)。

---

## 九、反馈与贡献

- Bug / 功能建议：[GitHub Issues](https://github.com/NinjaSln-labs/docs-search/issues)
- 安全漏洞：[SECURITY.md](https://github.com/NinjaSln-labs/docs-search/blob/main/SECURITY.md)（私密渠道）
- 想参与开发：[CONTRIBUTING.md](https://github.com/NinjaSln-labs/docs-search/blob/main/CONTRIBUTING.md) 与 [DEVELOPMENT.md](https://github.com/NinjaSln-labs/docs-search/blob/main/DEVELOPMENT.md)

---

*docs-search v1.1.0 · MIT License · [NinjaSln-labs/docs-search](https://github.com/NinjaSln-labs/docs-search)*
