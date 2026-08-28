"""Regular latitude/longitude grid construction for conservative ESMF remapping."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import numpy as np
from scipy.io import netcdf_file


def centers_to_bounds(values: np.ndarray, *, periodic: bool = False,
                      lower_limit: float | None = None,
                      upper_limit: float | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("coordinate centers must be a 1-D array with at least two values")
    d = np.diff(values)
    if not np.all(d > 0):
        raise ValueError("coordinate centers must be strictly increasing")
    edges = np.empty(values.size + 1, dtype=np.float64)
    edges[1:-1] = 0.5 * (values[:-1] + values[1:])
    edges[0] = values[0] - 0.5 * d[0]
    edges[-1] = values[-1] + 0.5 * d[-1]
    if lower_limit is not None:
        edges[0] = lower_limit
    if upper_limit is not None:
        edges[-1] = upper_limit
    if periodic:
        span = edges[-1] - edges[0]
        if not np.isclose(span, 360.0, atol=1.0e-6):
            raise ValueError(f"periodic longitude grid must span 360 degrees; got {span}")
    return np.column_stack((edges[:-1], edges[1:]))



def regular_cell_areas_sr(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Exact spherical cell areas [steradians] for a regular lat/lon grid."""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    lat_bnds = centers_to_bounds(lat, lower_limit=-90.0, upper_limit=90.0)
    lon_bnds = centers_to_bounds(lon, periodic=True)
    lat_factor = np.sin(np.deg2rad(lat_bnds[:, 1])) - np.sin(np.deg2rad(lat_bnds[:, 0]))
    dlon = np.deg2rad(lon_bnds[:, 1] - lon_bnds[:, 0])
    return lat_factor[:, None] * dlon[None, :]

def regular_grid_fingerprint(lat: np.ndarray, lon: np.ndarray) -> str:
    h = sha256()
    for arr in (np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64)):
        h.update(np.ascontiguousarray(arr).view(np.uint8))
    return h.hexdigest()[:16]


def write_gridspec_from_centers(lat: np.ndarray, lon: np.ndarray, out_file: str | Path) -> Path:
    """Write an ESMF GRIDSPEC file from monotonic regular-grid centers."""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    lat_bnds = centers_to_bounds(lat, lower_limit=-90.0, upper_limit=90.0)
    lon_bnds = centers_to_bounds(lon, periodic=True)
    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with netcdf_file(out, "w", version=2) as ds:
        ds.createDimension("lat", lat.size)
        ds.createDimension("lon", lon.size)
        ds.createDimension("bound", 2)
        vlat = ds.createVariable("lat", "f", ("lat",))
        vlon = ds.createVariable("lon", "f", ("lon",))
        vlatb = ds.createVariable("lat_bnds", "d", ("lat", "bound"))
        vlonb = ds.createVariable("lon_bnds", "d", ("lon", "bound"))
        vlat[:] = lat.astype(np.float32)
        vlon[:] = lon.astype(np.float32)
        vlatb[:] = lat_bnds
        vlonb[:] = lon_bnds
        vlat.standard_name = b"latitude"; vlat.axis = b"Y"; vlat.units = b"degrees_north"; vlat.bounds = b"lat_bnds"
        vlon.standard_name = b"longitude"; vlon.axis = b"X"; vlon.units = b"degrees_east"; vlon.bounds = b"lon_bnds"
        ds.grid_fingerprint = regular_grid_fingerprint(lat, lon).encode()
        ds.attribution = b"Original ESMF emissions-regridding utility lineage: Duseong Jo (2021)"
    return out
