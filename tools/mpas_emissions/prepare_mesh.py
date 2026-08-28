#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from .mesh import MpasMesh
from .scrip import write_mpas_scrip
from .locking import file_lock


def main():
    p=argparse.ArgumentParser(description='Prepare reusable MPAS emissions mesh products')
    p.add_argument('--mesh', required=True)
    p.add_argument('--cache-dir', required=True)
    p.add_argument('--mask-boundary-cells', action='store_true')
    a=p.parse_args()
    cache=Path(a.cache_dir); cache.mkdir(parents=True, exist_ok=True)
    mesh=MpasMesh.open(a.mesh)
    tag=f'x{mesh.n_cells}_{mesh.fingerprint}'
    neigh=cache/f'mpas_neighbors_{tag}.npz'
    scrip=cache/f'mpas_scrip_{tag}.nc'
    with file_lock(cache/'mesh_cache.lock'):
        if not neigh.exists(): mesh.save_neighbor_cache(neigh, interior_only=False)
        if mesh.vertices_on_cell is not None and mesh.lat_vertex is not None and mesh.lon_vertex is not None:
            if not scrip.exists(): write_mpas_scrip(mesh, scrip, mask_boundary_cells=a.mask_boundary_cells)
            scrip_msg=str(scrip)
        else:
            scrip_msg='SKIPPED (verticesOnCell/latVertex/lonVertex not present)'
    print(f'mesh_fingerprint={mesh.fingerprint}')
    print(f'nCells={mesh.n_cells}')
    print(f'surface_fraction={mesh.surface_fraction:.12g}')
    print(f'mesh_type={"global" if mesh.is_global else "regional"}')
    print(f'neighbors={neigh}')
    print(f'scrip={scrip_msg}')

if __name__ == '__main__': main()
