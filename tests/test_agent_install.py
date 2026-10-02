"""docs-search-install 一键安装器单元测试。

覆盖：各家配置格式合并（cursor/cline/opencode/commandcode/zcode/mcode）、幂等、--force 覆盖、
备份生成、dry-run 不落盘、CLI 调用适配器（mock，reasonix/qoder/pi）、dsh patch 追加。
用 monkeypatch 将 HOME 指向 tmp_path，隔离用户真实配置。
"""

import json
import subprocess
from pathlib import Path

import pytest

from docs_search import agent_install as ai


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 home() 指向 tmp_path 下的 fake-home，并隔离 shutil.which。"""
    h = tmp_path / "home"
    h.mkdir()

    def _home():
        return h

    monkeypatch.setattr(ai, "home", _home)
    monkeypatch.setattr(ai, "_cli_exists", lambda *names: False)
    # CLI 适配器默认不可用（避免真调 reasonix/qoder）
    return h


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- JSON 直写类

@pytest.mark.parametrize("cls,expect_path", [
    (ai.CursorInstaller, ".cursor/mcp.json"),
    (ai.ClineInstaller, ".cline/data/settings/cline_mcp_settings.json"),
    (ai.CommandCodeInstaller, ".commandcode/mcp.json"),
])
def test_json_mcpservers_install_and_idempotent(fake_home, cls, expect_path):
    inst = cls()
    p = fake_home / expect_path
    _write(p, {"mcpServers": {"other-server": {"command": "x"}}})

    assert inst.entry_status("local") == "absent"
    r1 = inst.install("local", "/docs/lib", force=False)
    assert "新增" in r1 and p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other-server"]["command"] == "x"  # 无关条目保留
    assert data["mcpServers"][ai.SERVER_NAME]["args"] == ["--dir", "/docs/lib"]

    # 幂等：重复 install 不重复加、不改内容
    before = p.read_text(encoding="utf-8")
    r2 = inst.install("local", "/docs/lib", force=False)
    assert "已存在" in r2
    assert p.read_text(encoding="utf-8") == before

    # --force 覆盖 + 备份
    r3 = inst.install("remote", "http://host:8765", force=True)
    assert "覆盖" in r3
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["mcpServers"][ai.SERVER_NAME]["args"] == ["--url", "http://host:8765"]
    assert p.with_name(p.name + ".bak").exists()  # 备份生成


def test_opencode_v2_servers_format(fake_home):
    inst = ai.OpenCodeInstaller()
    p = fake_home / ".config" / "opencode" / "opencode.jsonc"
    _write(p, {"mcp": {"servers": {"ctx": {"type": "remote", "url": "x"}}}})

    inst.install("local", "/docs", force=False)
    data = json.loads(p.read_text(encoding="utf-8"))
    s = data["mcp"]["servers"][ai.SERVER_NAME]
    assert s["type"] == "local"
    assert s["command"] == ["docs-search-mcp", "--dir", "/docs"]  # V2 命令数组
    assert data["mcp"]["servers"]["ctx"]["type"] == "remote"  # 无关条目保留


def test_zcode_servers_key_not_mcpservers(fake_home):
    """zcode 用 mcp.servers（不是 mcpServers）——键名错误会写进错误位置。"""
    inst = ai.ZCodeInstaller()
    p = fake_home / ".zcode" / "cli" / "config.json"
    _write(p, {"model": {"main": "tokenrouter/z-ai/glm-5.3-free"}, "mcp": {"servers": {}}})

    inst.install("remote", "https://srv", force=False)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert ai.SERVER_NAME in data["mcp"]["servers"]
    assert ai.SERVER_NAME not in data  # 不落在顶层
    assert data["model"]["main"] == "tokenrouter/z-ai/glm-5.3-free"  # 无关段保留
    assert data["mcp"]["servers"][ai.SERVER_NAME]["enable"] is True


def test_json_parse_error_skips_without_destroying(fake_home):
    """损坏/含注释 JSON（如 jsonc）解析失败 → 跳过并保留原文件。"""
    inst = ai.OpenCodeInstaller()
    p = fake_home / ".config" / "opencode" / "opencode.jsonc"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{ // jsonc comment\n  "mcp": {}\n}', encoding="utf-8")
    before = p.read_text(encoding="utf-8")

    r = inst.install("local", "/docs", force=False)
    assert "跳过" in r
    assert p.read_text(encoding="utf-8") == before  # 原文件未被破坏


def test_json_missing_file_creates(fake_home):
    inst = ai.CursorInstaller()
    r = inst.install("local", "/docs", force=False)
    assert "新增" in r
    assert inst.entry_status("local") == "exists"


def test_mcode_mcpservers_stdio(fake_home):
    """mcode：~/.minimax/mcp.json 的 mcpServers，stdio 条目含 type/enabled。"""
    inst = ai.McodeInstaller()
    p = fake_home / ".minimax" / "mcp.json"
    _write(p, {"mcpServers": {"other": {"type": "stdio", "command": "x"}}})

    assert inst.entry_status("local") == "absent"
    r = inst.install("local", "/docs", force=False)
    assert "新增" in r
    data = json.loads(p.read_text(encoding="utf-8"))
    s = data["mcpServers"][ai.SERVER_NAME]
    assert s["type"] == "stdio"
    assert s["command"] == "docs-search-mcp"
    assert s["args"] == ["--dir", "/docs"]
    assert s["enabled"] is True
    assert data["mcpServers"]["other"]["command"] == "x"  # 无关条目保留

    # 幂等
    assert "已存在" in inst.install("local", "/docs", force=False)


def test_extra_args_appended_to_config(fake_home):
    """--mcp-arg 透传：附加参数追加到各家配置的 args/command。"""
    extra = [("proxy", "direct"), ("token", "T")]
    # JSON 类（cursor：command/args 拆分）
    cur = ai.CursorInstaller()
    cur.install("remote", "https://srv", force=False, extra_args=extra)
    data = json.loads((fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"][ai.SERVER_NAME]["args"] == \
        ["--url", "https://srv", "--proxy", "direct", "--token", "T"]
    # OpenCode V2：command 数组
    oc = ai.OpenCodeInstaller()
    oc.install("local", "/docs", force=False, extra_args=extra)
    data = json.loads((fake_home / ".config" / "opencode" / "opencode.jsonc").read_text(encoding="utf-8"))
    assert data["mcp"]["servers"][ai.SERVER_NAME]["command"] == \
        ["docs-search-mcp", "--dir", "/docs", "--proxy", "direct", "--token", "T"]
    # dsh patch
    prof = fake_home / ".dsh" / "profiles" / "p"
    prof.mkdir(parents=True)
    (prof / "cordis.patch.yml").write_text("", encoding="utf-8")
    dsh = ai.DshInstaller()
    dsh.install("remote", "https://srv", force=False, extra_args=extra)
    txt = (prof / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "--proxy" in txt and "--token" in txt


def test_main_mcp_arg_cli(fake_home):
    _write(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    assert ai.main(["--url", "https://srv", "--agents", "cursor", "--mcp-arg", "proxy=direct"]) == 0
    data = json.loads((fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"][ai.SERVER_NAME]["args"] == ["--url", "https://srv", "--proxy", "direct"]


def test_main_mcp_arg_bad_format(fake_home, capsys):
    _write(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    assert ai.main(["--dir", "/docs", "--agents", "cursor", "--mcp-arg", "proxy"]) == 2
    assert "KEY=VALUE" in capsys.readouterr().out


# ---------------------------------------------------------------- 文件 patch 类

def test_dsh_patch_append_and_idempotent(fake_home):
    inst = ai.DshInstaller()
    prof = fake_home / ".dsh" / "profiles" / "headless"
    prof.mkdir(parents=True)
    patch = prof / "cordis.patch.yml"
    patch.write_text("- id: existing-entry\n", encoding="utf-8")

    r1 = inst.install("local", "/docs", force=False)
    assert "headless" in r1
    txt = patch.read_text(encoding="utf-8")
    assert ai.DshInstaller.entry_id in txt
    assert "docs-search-mcp" in txt
    assert "'--dir', '/docs'" in txt or '"--dir", "/docs"' in txt
    assert "existing-entry" in txt  # 原有条目保留

    # 幂等：重复跑不重复追加
    n1 = txt.count(ai.DshInstaller.entry_id)
    inst.install("local", "/docs", force=False)
    n2 = patch.read_text(encoding="utf-8").count(ai.DshInstaller.entry_id)
    assert n1 == n2 == 1

    # --force 仍只保留一条（替换而非重复追加）
    inst.install("remote", "http://h:8765", force=True)
    txt = patch.read_text(encoding="utf-8")
    assert txt.count(ai.DshInstaller.entry_id) == 1
    assert "--url" in txt


def test_dsh_no_profile_skips(fake_home):
    inst = ai.DshInstaller()
    r = inst.install("local", "/docs", force=False)
    assert "跳过" in r


def test_pi_mcp_cli_installer(fake_home, monkeypatch):
    """pi 新版内置 MCP：走 `pi mcp add`（不再是扩展 copy）。"""
    inst = ai.PiInstaller()
    monkeypatch.setattr(ai, "_cli_exists", lambda *names: True)
    runs = []

    def fake_run(args, timeout):
        runs.append(args)
        out = "" if "list" in args else "added docs-search"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(ai, "_run", fake_run)
    r = inst.install("local", "/docs", force=False)
    assert "added docs-search" in r
    assert runs[-1] == ["pi", "mcp", "add", "docs-search", "--", "docs-search-mcp", "--dir", "/docs"]

    # entry_status：list 输出含 docs-search → exists
    monkeypatch.setattr(ai, "_run",
                        lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout="docs-search", stderr=""))
    assert inst.entry_status("local") == "exists"


def test_pi_migrates_legacy_extension(fake_home, monkeypatch):
    """旧扩展安装 → install 先备份并移除扩展，再走 pi mcp add（扩展→MCP 迁移）。"""
    inst = ai.PiInstaller()
    ext = fake_home / ".pi" / "agent" / "extensions" / ai.PI_LEGACY_EXT
    ext.parent.mkdir(parents=True)
    ext.write_text("// legacy extension\n", encoding="utf-8")

    # entry_status/install 都会调 pi CLI，必须隔离——装有真实 pi 且已配置
    # docs-search 的机器上，不隔离会漏读真实 `pi mcp list` 输出
    monkeypatch.setattr(ai, "_run",
                        lambda args, timeout: subprocess.CompletedProcess(
                            args, 0, stdout="" if "list" in args else "added docs-search", stderr=""))

    assert inst.detect() is True  # 仅凭旧扩展即可检测到 pi
    assert inst.entry_status("local") == "legacy"

    r = inst.install("local", "/docs", force=False)
    assert not ext.exists()  # 扩展已移除
    assert ext.with_name(ext.name + ".bak").read_text(encoding="utf-8") == "// legacy extension\n"
    assert "已卸载旧扩展" in r and "added docs-search" in r


# ---------------------------------------------------------------- CLI 调用类

def test_cli_installer_calls_cli(fake_home, monkeypatch):
    inst = ai.ReasonixInstaller()
    monkeypatch.setattr(ai, "_cli_exists", lambda *names: True)
    calls = {}

    def fake_run(args, timeout):
        calls["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="added MCP server", stderr="")

    monkeypatch.setattr(ai, "_run", fake_run)
    r = inst.install("remote", "https://srv", force=False)
    assert "added MCP server" in r
    assert calls["args"] == ["reasonix", "mcp", "add", "docs-search", "--", "docs-search-mcp", "--url", "https://srv"]


def test_cli_installer_skips_when_exists(fake_home, monkeypatch):
    inst = ai.QoderInstaller()
    monkeypatch.setattr(ai, "_cli_exists", lambda *names: True)
    monkeypatch.setattr(ai, "_run",
                        lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout="docs-search", stderr=""))
    r = inst.install("local", "/docs", force=False)
    assert "已存在" in r


# ---------------------------------------------------------------- main() 流程

def test_main_list_and_dry_run(fake_home, capsys):
    # fake_home 下无任何 agent → 全部未检测
    assert ai.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "未检测到" in out

    # dry-run：无 agent 检测到 → 全部跳过，无写入
    assert ai.main(["--dir", "/docs", "--dry-run"]) == 0


def test_main_installs_detected_only(fake_home):
    # 模拟只安装了 cursor + cline
    _write(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    _write(fake_home / ".cline" / "data" / "settings" / "cline_mcp_settings.json", {"mcpServers": {}})

    assert ai.main(["--dir", "/docs/lib"]) == 0
    for rel in (".cursor/mcp.json", ".cline/data/settings/cline_mcp_settings.json"):
        data = json.loads((fake_home / rel).read_text(encoding="utf-8"))
        assert ai.SERVER_NAME in data["mcpServers"]
        assert data["mcpServers"][ai.SERVER_NAME]["args"] == ["--dir", "/docs/lib"]
    # 未安装的（如 opencode）不应被创建
    assert not (fake_home / ".config" / "opencode").exists()


def test_main_remote_mode_and_agents_subset(fake_home):
    _write(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    assert ai.main(["--url", "https://dsearch.example.com", "--agents", "cursor"]) == 0
    data = json.loads((fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"][ai.SERVER_NAME]["args"] == ["--url", "https://dsearch.example.com"]
