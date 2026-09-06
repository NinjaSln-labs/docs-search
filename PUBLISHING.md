# 发布指南

## PyPI 发布（维护者）

```bash
# 1. 版本号两处同步：pyproject.toml [project].version、src/docs_search/__init__.py __version__
# 2. CHANGELOG.md 增加 `[Unreleased]` 下的变更条目，发版时改为新版本号
# 3. 验证链全绿
python scripts/verify.py

# 4. 构建与发布（GitHub Actions 自动执行，也可手动）
pip install build twine
python -m build
twine upload dist/*
```

## 首次发布前置配置（一次性，Trusted Publishing）

首次推 `v*` tag 之前必须完成，否则 OIDC 发布会失败：

1. **PyPI 侧**：登录 pypi.org → Account settings → Publishing → Add a new pending publisher
   - PyPI project name: `docs-search`（首次发布用 pending 声明占位）
   - Owner: `NinjaSln-labs` · Repository: `docs-search` · Workflow: `publish.yml` · Environment: `pypi`
2. **GitHub 侧**：仓库 → Settings → Environments → New environment `pypi`（可加 tag 保护规则）
3. **建议先走 TestPyPI**：同样添加 pending publisher（TestPyPI 独立账号独立配置），改 `pypa/gh-action-pypi-publish` 的 `repository-url: https://test.pypi.org/legacy/` 验证链路后再发正式源

> 权限说明：`publish.yml` 顶层 `id-token: write` 是 repo-audit SEC-004 的要求；
> 上游若支持 job 级声明（NinjaSln-labs/repo-audit#2），可改回最小化 `permissions: {}` + publish job 内声明。

## CI 自动发布

推 tag `v*`（如 `v1.0.1`）触发 `.github/workflows/publish.yml`：
1. checkout + setup-python（SHA 固定）
2. `python -m build` + `twine check`
3. PyPI 发布（Trusted Publishing，无 token）

## 发布前检查单

- [ ] `pyproject.toml` / `__init__.py` 版本一致
- [ ] 验证链全绿
- [ ] README 快速开始与新 API 同步
- [ ] tag 指向已合并到 main 的提交
