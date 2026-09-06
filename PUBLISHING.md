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

首次推 `v*` tag 之前必须完成，否则 OIDC 发布会失败（参照 repo-audit 分类模板
`templates/categories/python-app/PUBLISHING.md`）：

1. **GitHub 侧**：仓库 → Settings → Environments → 建 `pypi` 与 `testpypi` 两个 environment，
   **必须开 required reviewers**（PyPA guide：每次发布需人工批准）
2. **PyPI 侧**：账号 → account publishing → 登记 pending publisher：项目名 + owner/repo +
   workflow 文件名 `publish.yml` + environment `pypi`；首次发布成功自动建项目。
   ⚠️ pending **不保留项目名**，发布前被抢注则失效，建完尽快发
3. **建议先走 TestPyPI**：test.pypi.org 单独账号单独重复上述登记，灰度验证链路后再发正式源
4. （建议）tag protection：限制 `v*` tag 只能由维护者推送

## CI 自动发布

## CI 自动发布

推 tag `v*`（如 `v1.0.1`）触发 `.github/workflows/publish.yml`：
1. checkout + setup-python（SHA 固定）
2. `python -m build` + `twine check`
3. PyPI 发布（Trusted Publishing，无 token；权限最小化——id-token 仅 publish job 持有）

## 发布前检查单

- [ ] `pyproject.toml` / `__init__.py` 版本一致
- [ ] 验证链全绿
- [ ] README 快速开始与新 API 同步
- [ ] tag 指向已合并到 main 的提交
