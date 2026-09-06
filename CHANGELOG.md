# Changelog

本文件记录所有对外可见的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本遵循 SemVer。

## [Unreleased]

### Changed

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
