"""docs-search MCP server 直跑入口（瘦包装）。

优先使用已安装的 docs_search 包（pip install docs-search）；
未安装时回退到仓库内 src/ 布局，保持 `python scripts/docs-search-mcp.py` 直跑可用。
供 MCP 客户端（cursor / zcode / qorder / dsh 等）以 stdio 方式启动：
  python scripts/docs-search-mcp.py --dir <文档目录>
"""

import sys
from pathlib import Path

try:
    from docs_search.mcp import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from docs_search.mcp import main

if __name__ == "__main__":
    main()
