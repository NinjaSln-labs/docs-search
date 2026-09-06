# 发布指南

## PyPI 发布（维护者）

```bash
# 1. 版本号三处同步：pyproject.toml [project].version、src/docs_search/__init__.py __version__、CHANGELOG
# 2. 验证链全绿
python -m pytest && ruff check src/ tests/ scripts/

# 3. 构建与发布（GitHub Actions 自动执行，也可手动）
pip install build twine
python -m build
twine upload dist/*
```

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
