#!/usr/bin/env python3
"""docs-search 验证链单源：lint + 全量测试，全绿 exit 0。

用法:
  python scripts/verify.py          # ruff check + pytest
  python scripts/verify.py --fast   # 跳过 lint 只跑测试

提交前必须全绿（pre-commit / CI / AGENTS.md 三处同源引用本文件）。
"""
import subprocess
import sys

STEPS = [
    ('ruff check', [sys.executable, '-m', 'ruff', 'check', 'src/', 'tests/', 'scripts/']),
    ('pytest', [sys.executable, '-m', 'pytest']),
]


def main():
    fast = '--fast' in sys.argv
    failed = []
    for name, cmd in STEPS:
        if fast and name == 'ruff check':
            continue
        print(f'[verify] {name} ...')
        r = subprocess.run(cmd, check=False)
        if r.returncode != 0:
            failed.append(name)
    if failed:
        print(f'[verify] FAIL: {", ".join(failed)}')
        sys.exit(1)
    print('[verify] all green')


if __name__ == '__main__':
    main()
