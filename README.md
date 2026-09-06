# docs-search

零依赖的本地文档搜索引擎。纯 Python 标准库，SQLite 索引，毫秒级检索。

A zero-dependency local document search engine. Pure Python stdlib, SQLite index, millisecond-level retrieval.

## 特性 / Features

- **零依赖** — 纯 Python 标准库（3.8+），无需 pip install
- **快** — SQLite FTS 索引，检索 < 50ms
- **自动索引** — 搜索前自动检测文件变更并增量重建
- **Web UI** — 内置搜索界面 + 拖拽上传 .md 文档
- **多库隔离** — 不同文档目录各自独立索引，可并存
- **不绑定路径** — 文档目录由参数/环境变量指定，不写死任何本地路径
- **跨平台** — Windows / macOS / Linux

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

## 安全说明

- 服务默认仅监听 `127.0.0.1`，**请勿用 `--host 0.0.0.0` 暴露到公网**（接口无鉴权）
- 上传仅接受 `.md` 文件、单文件 ≤ 10MB、文件名经过消毒（防路径穿越）
- 删除接口仅允许操作 `uploads/` 目录内的文件

## 许可证 / License

MIT — 见 [LICENSE](LICENSE)
