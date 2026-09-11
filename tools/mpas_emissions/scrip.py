"""Create an ESMF/SCRIP destination-grid file directly from an MPAS mesh.

This removes the need for a separately maintained SCRIP file in the emissions
workflow.  It is intended for use with conservative CAMS -> MPAS regridding.
"""

from __future__ import annotations

from pathlib import Path
import numpy as np
import xarray as xr

from .mesh import MpasMesh


def _to_2pi(rad: np.ndarray) -> np.ndarray:
    """Wrap radians to [0, 2*pi), matching MPAS cell-center convention."""
    return np.mod(rad, 2.0 * np.pi)


def write_mpas_scrip(
    mesh: MpasMesh | str | Path,
    out_file: str | Path,
    *,
    mask_boundary_cells: bool = False,
) -> Path:
    """Write a one-dimensional SCRIP mesh using MPAS cell polygons.

    The SCRIP element ordering is identical to MPAS ``nCells``. Cells with
    fewer than the maximum *active* number of MPAS edges are padded by repeating
    their last valid vertex.  This reproduces the ``mpas2esmf`` convention used
    by the production MPAS emissions SCRIP files.

    Geometry is written in radians.  MPAS cell-center longitudes are wrapped to
    [0, 2*pi), while vertex longitudes retain the native MPAS [-pi, pi] convention.
    """
    if not isinstance(mesh, MpasMesh):
        mesh = MpasMesh.open(mesh)
    if mesh.vertices_on_cell is None or mesh.lat_vertex is None or mesh.lon_vertex is None:
        raise KeyError(
            "MPAS mesh must contain verticesOnCell, latVertex and lonVertex "
            "to construct a conservative-regridding SCRIP file"
        )

    n_cells = mesh.n_cells
    # ``verticesOnCell`` can have a storage dimension larger than the actual
    # polygon order (e.g. maxEdges=10 while this mesh contains only pentagons
    # and hexagons).  SCRIP must use the maximum active polygon order.
    max_corners = int(np.max(mesh.n_edges_on_cell))
    corner_lat = np.empty((n_cells, max_corners), dtype=np.float64)
    corner_lon = np.empty((n_cells, max_corners), dtype=np.float64)

    for cell in range(n_cells):
        nedge = int(mesh.n_edges_on_cell[cell])
        raw = np.asarray(mesh.vertices_on_cell[cell, :nedge], dtype=np.int64)
        valid = raw[(raw > 0) & (raw <= mesh.lat_vertex.size)] - 1
        if valid.size < 3:
            raise ValueError(f"Cell {cell} has fewer than 3 valid vertices")
        lat = np.asarray(mesh.lat_vertex[valid], dtype=np.float64)
        lon = np.asarray(mesh.lon_vertex[valid], dtype=np.float64)
        corner_lat[cell, : valid.size] = lat
        corner_lon[cell, : valid.size] = lon
        if valid.size < max_corners:
            corner_lat[cell, valid.size :] = lat[-1]
            corner_lon[cell, valid.size :] = lon[-1]

    grid_imask = np.ones(n_cells, dtype=np.int32)
    if mask_boundary_cells:
        grid_imask[~mesh.interior_mask] = 0

    # SCRIP convention: grid_area is in steradians when provided.
    area_sr = mesh.area_cell / (mesh.sphere_radius_m ** 2)

    ds = xr.Dataset(
        data_vars={
            "grid_dims": (("grid_rank",), np.asarray([n_cells], dtype=np.int32)),
            "grid_center_lat": (("grid_size",), np.asarray(mesh.lat_cell, dtype=np.float64)),
            "grid_center_lon": (("grid_size",), _to_2pi(np.asarray(mesh.lon_cell, dtype=np.float64))),
            "grid_imask": (("grid_size",), grid_imask),
            "grid_corner_lat": (("grid_size", "grid_corners"), corner_lat),
            "grid_corner_lon": (("grid_size", "grid_corners"), corner_lon),
            "grid_area": (("grid_size",), area_sr.astype(np.float64)),
        },
        attrs={
            "title": "SCRIP grid generated directly from an MPAS mesh",
            "source_mesh": str(mesh.path),
            "mesh_fingerprint": mesh.fingerprint,
            "attribution": (
                "Emission regridding methodology follows Duseong Jo's original "
                "ESMF regridding utilities (2021)."
            ),
        },
    )
    ds["grid_center_lat"].attrs["units"] = "radians"
    ds["grid_center_lon"].attrs["units"] = "radians"
    ds["grid_corner_lat"].attrs["units"] = "radians"
    ds["grid_corner_lon"].attrs["units"] = "radians"
    ds["grid_area"].attrs["units"] = "radians^2"

    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Classic NetCDF matches the historical ``mpas2esmf`` SCRIP products.
    ds.to_netcdf(out, engine="scipy", format="NETCDF3_CLASSIC")
    ds.close()
    return out
