"""docs-search Web UI 直跑入口（瘦包装）。

优先使用已安装的 docs_search 包（pip install docs-search）；
未安装时回退到仓库内 src/ 布局，保持 `python scripts/docs-search-web.py` 直跑可用。
"""

import sys
from pathlib import Path

try:
    from docs_search.web import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from docs_search.web import main

if __name__ == "__main__":
    main()
