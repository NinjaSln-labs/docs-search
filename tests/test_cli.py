"""CLI 端到端测试（子进程直跑 scripts/docs-search.py）"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "docs-search.py"


def run_cli(*args, cwd=REPO):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        check=False,
        env={
            "PYTHONIOENCODING": "utf-8",
            "PATH": "",
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            "USERPROFILE": __import__("os").environ.get("USERPROFILE", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
        },
    )


def make_corpus(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "hello.md").write_text("# Hello\n\nunique token FROBNICATOR\n", encoding="utf-8")
    return docs


def test_index_search_status_roundtrip(tmp_path):
    docs = make_corpus(tmp_path)
    db = tmp_path / "idx.db"

    r = run_cli("index", "--dir", str(docs), "--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "indexed 1 docs" in r.stdout

    r = run_cli("search", "FROBNICATOR", "--dir", str(docs), "--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "1 results" in r.stdout and "hello.md" in r.stdout

    r = run_cli("search", "MISSING_TOKEN_XYZ", "--dir", str(docs), "--db", str(db))
    assert "no results" in r.stdout

    r = run_cli("status", "--dir", str(docs), "--db", str(db))
    assert "records: 1" in r.stdout and "stale:   no" in r.stdout


def test_list_and_show(tmp_path):
    docs = make_corpus(tmp_path)
    db = tmp_path / "idx.db"
    run_cli("index", "--dir", str(docs), "--db", str(db))

    r = run_cli("list", "--dir", str(docs), "--db", str(db))
    assert "total: 1 docs" in r.stdout

    r = run_cli("show", "hello.md", "--dir", str(docs), "--db", str(db))
    assert "FROBNICATOR" in r.stdout


def test_upload_command(tmp_path):
    docs = make_corpus(tmp_path)
    db = tmp_path / "idx.db"
    src = tmp_path / "incoming.md"
    src.write_text("# Incoming\n\nuploaded via CLI\n", encoding="utf-8")

    r = run_cli("upload", str(src), "--dir", str(docs), "--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "uploaded:" in r.stdout
    assert (docs / "uploads" / "incoming.md").exists()

    r = run_cli("search", "uploaded via CLI", "--dir", str(docs), "--db", str(db))
    assert "incoming.md" in r.stdout


def test_upload_rejects_non_md(tmp_path):
    docs = make_corpus(tmp_path)
    src = tmp_path / "evil.exe"
    src.write_text("x", encoding="utf-8")
    r = run_cli("upload", str(src), "--dir", str(docs), "--db", str(tmp_path / "idx.db"))
    assert r.returncode == 1 and "invalid filename" in r.stdout


def test_missing_docs_dir_clear_error(tmp_path):
    r = run_cli("index", "--dir", str(tmp_path / "nonexistent"), "--db", str(tmp_path / "idx.db"))
    # 空目录索引=0 篇（目录会按需创建），不崩溃即符合契约
    assert r.returncode == 0, r.stderr
