"""Command-line entry points for data retrieval."""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from riskdyn.sources.d12.fetch import D12Client
from riskdyn.sources.d12.parse_catalog import parse_catalog


def _pull_catalog(out: pathlib.Path) -> int:
    client = D12Client()
    try:
        maps = parse_catalog(client.get_text("/maps"))
    finally:
        client.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([dataclasses.asdict(m) for m in maps], indent=1))
    print(f"wrote {len(maps)} maps to {out}")
    return 0


def _pull_images(out_dir: pathlib.Path, limit: int | None) -> int:
    client = D12Client()
    try:
        maps = parse_catalog(client.get_text("/maps"))
        maps.sort(key=lambda m: -m.num_games_total)
        if limit is not None:
            maps = maps[:limit]
        out_dir.mkdir(parents=True, exist_ok=True)
        for summary in maps:
            path = f"/assets/img/maps/{summary.map_id}.large.jpg"
            (out_dir / f"{summary.map_id}.large.jpg").write_bytes(client.get(path))
            print(f"  {summary.map_id:>3}  {summary.name}")
    finally:
        client.close()
    print(f"wrote {len(maps)} images to {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riskdyn")
    sub = parser.add_subparsers(dest="command")

    catalog = sub.add_parser("pull-catalog", help="fetch the map catalog")
    catalog.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw/map_catalog.json"))

    images = sub.add_parser("pull-images", help="fetch map images, most-played first")
    images.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw/map_images"))
    images.add_argument("--limit", type=int, default=None)

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if e.code is not None else 2

    if args.command == "pull-catalog":
        return _pull_catalog(args.out)
    if args.command == "pull-images":
        return _pull_images(args.out, args.limit)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
