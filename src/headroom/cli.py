"""Command line entry point: `uv run headroom <command>`."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="headroom", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Rebuild the fictional demo snapshot in data/snapshots/demo")
    sub.add_parser("refresh", help="Refresh live snapshots from public sources")
    args = parser.parse_args(argv)

    if args.cmd == "demo":
        from headroom.demo.generate import write

        write()
        print("Demo snapshot written to data/snapshots/demo")
        return 0
    if args.cmd == "refresh":
        import logging

        from headroom.pipeline import build_live

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        meta = build_live()
        print(
            f"Live snapshot: {meta['issuers_property']} property issuers "
            f"of {meta['issuers_swedish']} Swedish bond issuers"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
