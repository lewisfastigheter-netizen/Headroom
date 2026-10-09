"""Command line entry point: `uv run headroom <command>`."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="headroom", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Rebuild the fictional demo snapshot in data/snapshots/demo")
    sub.add_parser("refresh", help="Refresh live snapshots from public sources")
    sub.add_parser("locations", help="Rebuild data/snapshots/locations (SCB, Kolada, geodata) only")
    sub.add_parser(
        "private", help="Check the next batch of private companies at Bolagsverket (daily job)"
    )
    sub.add_parser("private-status", help="Print the private-company scan progress (Markdown)")
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
        # Location statistics: SCB tables that have not been updated are not fetched again.
        try:
            from headroom.location_pipeline import build_locations

            out = build_locations()
            print(f"Locations snapshot: {out['places']} places")
        except ImportError as e:  # shapely (the geo group) not installed
            print(f"Locations skipped: {e}. Run `uv sync` (installs the geo group).")
        except Exception as e:  # a source is down: keep the live snapshot, keep the old one
            logging.getLogger(__name__).exception("Locations step failed: %s", e)
        return 0
    if args.cmd == "locations":
        import logging

        from headroom.location_pipeline import build_locations

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        out = build_locations()
        print(f"Locations snapshot: {out['places']} places, {out['indicators']} indicator rows")
        return 0
    if args.cmd == "private-status":
        from headroom.store.private import progress_markdown

        print(progress_markdown())
        return 0
    if args.cmd == "private":
        import logging

        from headroom.pipeline import update_private

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        stats = update_private()
        print(
            f"Private companies: {stats.get('private_new_lookups', 0)} checked this run, "
            f"{stats.get('private_kept', 0)} kept in total of "
            f"{stats.get('bulk_candidates', 0)} candidates"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
