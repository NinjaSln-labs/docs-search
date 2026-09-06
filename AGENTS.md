# AGENTS（AI 协作与工程纪律）

> 改编自 repo-audit `templates/common/AGENTS-core.md`（上游 scaffold `--update` 亦可为存量仓生成/合并；
> 本文件为**手写自主维护**，可直接编辑）。上游模板修订需手动同步；适配差异见上游反馈
> NinjaSln-labs/repo-audit#3。人工协作者同样适用本文件全部条款。

## 项目概览

docs-search：零依赖本地文档搜索引擎（Python 标准库 + SQLite，CLI + Web UI，含上传接口）。仓库根即工程根；分支、提交、验证、发版规范见下与 CONTRIBUTING.md。

## 提交规范

- **Conventional Commits 前缀 + 中文描述**：`feat(scope):` / `fix(scope):` / `refactor:` / `docs:` / `test:` / `chore:`；scope 用模块名（core / cli / web / tests / docs）；发布提交固定 `chore: release v<版本> — <一句话主旨>`。
- **提交前必须跑本仓验证链单源**：`python -m pytest && ruff check src/ tests/ scripts/`，全绿才可提交。CI 与本地同源。FAIL 修根因，不绕过；确需 `--no-verify` 必须在提交说明注明原因。

## AI 协作守则（agent 贡献者必读）

1. **不猜 API/契约**：写代码前用宿主/依赖的检查工具查精确签名；测试 stub 必须按真实契约形状写（失真的 stub 会掩盖契约 bug）。
2. **完成的定义 = 验证链全绿 + 实机/测试验收**，不是"代码写完"；声称完成前附验证输出。
3. **机密红线**：本机绝对路径、个人邮箱、token/密钥、会过时的部署实况描述一律不入库；提交前 `git grep` 自查。
4. **不静默绕过门禁**：pre-commit/CI FAIL 先修根因；中间态确需跳过必须留痕注明。
5. **改动最小化**：不顺手重构、不改无关文件；构建产物与锁文件按仓库既定规则处理。
6. **文档同步**：行为/接口变化同步 README、DEVELOPMENT 速查表、CHANGELOG。
7. **冲突处理**：本文件与生成它的模板源冲突时以模板源为准并回写；用户显式指示优先于本文件，但需在 PR/提交说明中标注冲突点。

## 分类纪律（python-app）

### 验证链单源

```bash
python -m pytest          # tests/ 单元 + CLI E2E + Web API（41 用例）
ruff check src/ tests/ scripts/
```

测试与 lint 全绿 = 验证链通过。CI（`.github/workflows/ci.yml`）与本地同源。

### 机密自查模式（提交前 git grep）

```bash
git grep -nIE "(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|AIza[A-Za-z0-9_-]{35}|xox[bap]-)" && echo "疑似密钥" || echo OK
git grep -nIE "(C:\\\\Users\\\\[a-zA-Z]|/Users/[a-zA-Z]|E:\\\\PiWorkspace)" && echo "疑似本机路径" || echo OK
```

### 上传接口安全红线

- 上传仅接受 `.md`、≤10MB、文件名消毒（防路径穿越）；删除仅限 `uploads/`
- 服务默认 `127.0.0.1`；改动监听/文件操作逻辑必须同步补测试（`tests/test_web.py` 安全用例不得删）

## 安全考虑

- 漏洞**不要**公开披露：走 SECURITY.md 指定的私密漏洞报告渠道。
- 依赖与 CI action 升级走仓库既定自动化（如有）；引入新依赖需在 PR 说明中给出理由（本项目运行时依赖应为空）。
