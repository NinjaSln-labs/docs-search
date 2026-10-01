# 工作区规则

## 通用规则

1. 仓库根即工程根；分支/发版细节见 CONTRIBUTING.md。
2. 提交用 Conventional Commits 前缀 + 中文描述：`feat|fix|refactor|docs|test|chore(scope):`；scope 用模块名（core / cli / web / tests / docs）；发布提交固定 `chore: release v<版本> — <一句话主旨>`。
3. 完成 = 验证链全绿 + 验收输出；声称完成前附验证输出。
4. 查精确签名再写代码，不猜 API/契约；测试 stub 按真实契约形状写。
5. 只改与任务相关的文件，不顺手重构。
6. 行为/接口变化同步 README、DEVELOPMENT、CHANGELOG。

## 硬约束

- 本机路径、个人邮箱、token/密钥一律不写进提交；外部凭据处写占位符 `your-key-here`（本仓零运行时依赖，无凭据需求；发版走 Trusted Publishing，无 token 落盘）。
- pre-commit/CI FAIL 先修根因；确需跳过（如 `--no-verify`）在提交说明注明原因。
- 漏洞不公开披露，走 SECURITY.md 私密渠道。
- 引入新依赖需在 PR 说明理由（本仓运行时依赖应为空）。
- 上传接口：只接受 `.md`、≤10MB、文件名消毒；删除仅限 `uploads/`；服务默认监听 `127.0.0.1`；改 `src/docs_search/web.py` 或 `core.py` 必须跑 `pytest tests/test_web.py` 且不删安全用例。

## 命令

验证链（提交前必须全绿）：

```bash
python -m pytest
ruff check src/ tests/ scripts/
```

机密自查（提交前 git grep）：

```bash
git grep -nIE "(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|AIza[A-Za-z0-9_-]{35}|xox[bap]-)" && echo "疑似密钥" || echo OK
git grep -nIE "(C:\\\\Users\\\\[a-zA-Z]|/Users/[a-zA-Z]|/home/[a-zA-Z_]+)" && echo "疑似本机路径" || echo OK
```

## 内容落位

- 未决项 → `.handoff/actions/<域>.jsonl`（`.handoff/` 已 gitignore，本地存储不入库）；候选待办 → PR/issue。
- 发版细节 → PUBLISHING.md；项目介绍 → README.md，均不进本文件。
