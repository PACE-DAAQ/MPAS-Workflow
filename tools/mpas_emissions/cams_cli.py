#!/usr/bin/env python3
"""Run workflow-native CAMS anthropogenic or biogenic processing."""
from __future__ import annotations

import argparse
from pathlib import Path

from .cams_regrid import CamsProcessor
from .mesh import MpasMesh
from .locking import file_lock


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("config", help="CAMS YAML configuration")
    p.add_argument("--kind", choices=["anth", "biog"], required=True)
    p.add_argument("--mesh", required=True)
    p.add_argument("--cache-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--weights", default="", help="optional existing ESMF weight file")
    p.add_argument("--year", type=int, default=None, help="override config year (normally the Cylc cycle year)")
    p.add_argument("--grid-name", default="", help="MPAS grid label, e.g. x1.163842 or x6.828394")
    p.add_argument("--chunk-links", type=int, default=2_000_000)
    p.add_argument("--conservation-tolerance", type=float, default=5.0e-5)
    p.add_argument("--reuse-existing", action="store_true")
    args = p.parse_args()

    mesh = MpasMesh.open(args.mesh)
    proc = CamsProcessor(
        mesh=mesh,
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        provided_weight_file=Path(args.weights) if args.weights else None,
        chunk_links=args.chunk_links,
        global_conservation_tolerance=args.conservation_tolerance,
        grid_name=args.grid_name or None,
    )
    lock = Path(args.cache_dir) / f"cams_{args.kind}.lock"
    with file_lock(lock):
        outs = proc.process_config(args.config, kind=args.kind, reuse_existing=args.reuse_existing, year_override=args.year)
    for out in outs:
        print(out)


if __name__ == "__main__":
    main()
