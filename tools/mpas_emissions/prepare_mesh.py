#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from .mesh import MpasMesh
from .scrip import write_mpas_scrip
from .locking import file_lock
from .cache_paths import resolve_grid_name, scrip_file, neighbors_file, cache_is_usable


def _neighbors_are_usable(path: Path, mesh_fingerprint: str) -> bool:
    """Reuse a neighbor cache only when it was built from this mesh.

    The npz stores ``mesh_fingerprint`` alongside the table, which is what the
    filename used to carry.
    """
    if not path.exists():
        return False
    try:
        with np.load(path, allow_pickle=False) as z:
            stored = str(z["mesh_fingerprint"]) if "mesh_fingerprint" in z else None
    except Exception:
        stored = None
    if stored is None:
        return True
    if stored == mesh_fingerprint:
        return True
    print(f"WARNING cached neighbor table {path.name} was built from mesh {stored}, "
          f"current mesh is {mesh_fingerprint}; regenerating")
    return False


def main():
    p=argparse.ArgumentParser(description='Prepare reusable MPAS emissions mesh products')
    p.add_argument('--mesh', required=True)
    p.add_argument('--cache-dir', required=True)
    p.add_argument('--grid-name', default='', help='MPAS grid label, e.g. x1.163842 or x6.828394')
    p.add_argument('--mask-boundary-cells', action='store_true')
    a=p.parse_args()
    cache=Path(a.cache_dir); cache.mkdir(parents=True, exist_ok=True)
    mesh=MpasMesh.open(a.mesh)
    grid_name=resolve_grid_name(a.grid_name or None, mesh.n_cells)
    neigh=neighbors_file(cache, grid_name)
    scrip=scrip_file(cache, grid_name)
    with file_lock(cache/'mesh_cache.lock'):
        if not _neighbors_are_usable(neigh, mesh.fingerprint):
            mesh.save_neighbor_cache(neigh, interior_only=False)
        if mesh.vertices_on_cell is not None and mesh.lat_vertex is not None and mesh.lon_vertex is not None:
            if not cache_is_usable(scrip, {"mesh_fingerprint": mesh.fingerprint}, label="cached SCRIP mesh"):
                write_mpas_scrip(mesh, scrip, mask_boundary_cells=a.mask_boundary_cells)
            scrip_msg=str(scrip)
        else:
            scrip_msg='SKIPPED (verticesOnCell/latVertex/lonVertex not present)'
    print(f'grid_name={grid_name}')
    print(f'mesh_fingerprint={mesh.fingerprint}')
    print(f'nCells={mesh.n_cells}')
    print(f'surface_fraction={mesh.surface_fraction:.12g}')
    print(f'mesh_type={"global" if mesh.is_global else "regional"}')
    print(f'neighbors={neigh}')
    print(f'scrip={scrip_msg}')

if __name__ == '__main__': main()
