#!/usr/bin/env python3
"""Process a config-driven regular-grid inventory (CEDS/GFAS/QFED/etc.) to MPAS."""
from __future__ import annotations
import argparse
from pathlib import Path
from .mesh import MpasMesh
from .regular_inventory import RegularInventoryProcessor
from .locking import file_lock


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("config")
    p.add_argument("--mesh", required=True); p.add_argument("--cache-dir", required=True); p.add_argument("--output-dir", required=True)
    p.add_argument("--weights", default=""); p.add_argument("--year", type=int, default=None); p.add_argument("--grid-name", default="")
    p.add_argument("--chunk-links", type=int, default=2_000_000); p.add_argument("--conservation-tolerance", type=float, default=5e-5)
    p.add_argument("--reuse-existing", action="store_true")
    a=p.parse_args()
    mesh=MpasMesh.open(a.mesh)
    proc=RegularInventoryProcessor(mesh,Path(a.cache_dir),Path(a.output_dir),a.chunk_links,a.conservation_tolerance,
                                   Path(a.weights) if a.weights else None, a.grid_name or None)
    lock=Path(a.cache_dir)/(Path(a.config).stem+".regular.lock")
    with file_lock(lock): outs=proc.process(a.config,year_override=a.year,reuse_existing=a.reuse_existing)
    for x in outs: print(x)

if __name__=="__main__": main()
