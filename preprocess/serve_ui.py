#!/usr/bin/env python
"""Start the local WellPrep operation UI.

    python preprocess/serve_ui.py --port 8768
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from wellprep.webapp import serve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="WellPrep local web UI")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8768, help="port (default 8768)")
    ap.add_argument("--open", action="store_true", help="open the browser after start")
    args = ap.parse_args()
    serve(args.host, args.port, args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
