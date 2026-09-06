# 开发指南

## 结构速查

```
src/docs_search/
  core.py     # 扫描、索引、路径解析、上传文件名安全化（CLI/Web 共享核心）
  cli.py      # 命令行入口（index/search/list/show/status/upload/open）
  web.py      # HTTP 服务 + Web UI（含 /api/upload、/api/delete）
scripts/      # 瘦包装直跑入口（优先用已安装包，回退 src/）
tests/        # pytest：单元 + CLI E2E（子进程）+ Web API（内存 HTTP）
```

## 常用命令

```bash
pip install -e .                    # 可编辑安装
python -m pytest                    # 全部测试
python -m pytest tests/test_web.py  # 只跑 Web API
ruff check src/ tests/ scripts/     # lint
ruff format src/ tests/ scripts/    # 格式化
python scripts/docs-search.py search "kw" --dir <目录>   # 直跑 CLI
python scripts/docs-search-web.py <目录> --no-browser    # 直跑 Web
```

## 路径解析契约（不绑定本地路径）

- 文档目录：`--dir` > `$DOCS_SEARCH_DIR` > `./docs`
- 索引库：`--db` > `$DOCS_SEARCH_DB` > `~/.docs-search/<目录哈希>/index.db`
- **禁止**在源码中出现任何个人/本机绝对路径（测试 `test_no_hardcoded_paths` 会拦截）

## 设计约束

- **零运行时依赖**：`src/` 只 import 标准库；开发工具（pytest/ruff）只进 `[dependency-groups]`
- **索引库隔离**：库路径 = 目录哈希，多文档库并存互不污染；重建必须幂等（`DROP TABLE IF EXISTS`）
- **Windows 兼容**：控制台输出强制 UTF-8；HTTPServer 禁用 `SO_REUSEADDR` 防多进程重复绑定
- **上传安全**：`.md` only、≤10MB、文件名消毒、删除仅限 `uploads/`——安全测试用例不得删减

## 发布

维护者流程见 [PUBLISHING.md](PUBLISHING.md)。
