#!/usr/bin/env python3
"""docs-search-install 瘦包装（优先已安装包，回退 src/ 直跑）——与 scripts/ 其他入口一致。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    from docs_search.agent_install import main
except ImportError:
    from docs_search.cli import main  # pragma: no cover  # 不应发生

if __name__ == "__main__":
    sys.exit(main())
