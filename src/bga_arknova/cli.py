from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(prog="bga-arknova")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scrape", help="抓取 BGA 战绩与回放")
    s.add_argument("--player-id", type=int, required=True)
    s.add_argument("--player-name", default="")
    s.add_argument("--history-only", action="store_true")
    s.add_argument("--limit", type=int)
    s.add_argument("--delay", type=float, default=0.2)
    s.add_argument("--workers", type=int, default=5)
    s.add_argument("--refresh", action="store_true")
    sub.add_parser("dashboard", help="启动 Streamlit 面板")
    args = parser.parse_args()
    root = project_root()
    if args.command == "scrape":
        from .scraper import scrape
        asyncio.run(scrape(root, args.player_id, args.player_name, args.history_only, args.limit, args.delay, args.workers, args.refresh))
    else:
        from streamlit.web import cli as stcli
        import sys
        app = Path(__file__).with_name("streamlit_app.py")
        sys.argv = ["streamlit", "run", str(app), "--", str(root)]
        raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()

