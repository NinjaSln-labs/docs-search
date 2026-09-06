# 贡献指南

感谢关注 docs-search！本项目追求**零运行时依赖**，请遵守以下约定。

## 开发环境

```bash
git clone https://github.com/ninjasln-labs/docs-search.git
cd docs-search
pip install -e .           # 可编辑安装（含 docs-search / docs-search-web 命令）
pip install -r requirements.lock    # 开发工具链（锁定版本）
```

## 验证链（提交前必须全绿）

```bash
python scripts/verify.py           # 验证链单源：ruff + pytest
ruff check src/ tests/ scripts/
```

## 提交规范

- Conventional Commits + 中文描述：`feat(core): xxx` / `fix(web): yyy` / `docs: zzz`
- 一个提交一个主旨，禁止无关文件混入
- 声称完成 = 验证链全绿 + 附验证输出

## 代码约定

- Python ≥ 3.10，只用标准库（`src/docs_search/` 不得引入第三方 import）
- 模块职责：`core.py`（扫描/索引/上传逻辑）· `cli.py`（命令行）· `web.py`（HTTP 服务）
- 行为变更必须同步测试与 README；改安全相关逻辑（上传/删除/路径）必须同步 `tests/test_web.py` 的安全用例

## 报告问题

- Bug：附最小复现（命令 + 输出 + 环境）
- 安全漏洞：**勿开公开 Issue**，见 [SECURITY.md](SECURITY.md)
