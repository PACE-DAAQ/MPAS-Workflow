"""Cache filenames for reusable mesh/grid regridding products, and the
identity checks that replace the fingerprints those filenames used to carry.

Cache artifacts used to be named by content hash, e.g.::

    mpas_scrip_x163842_31a772cf2179c361.nc
    weights_src_a3f19c2e91b4d077_to_mpas_31a772cf2179c361_conserve.nc

which made the name self-validating but unreadable, and left the workflow with
two unrelated spellings for one mesh: ``x1.163842`` in every MPAS-facing file
and ``x163842_<hash>`` in the cache.  Names here use the MPAS grid label and
the source-grid shape instead::

    mpas_scrip_x1.163842.nc
    gridspec_1800x3600.nc
    weights_1800x3600_to_x1.163842.nc

Identity is not lost, it moves into the file: every product carries the
fingerprint of what produced it as a NetCDF attribute (``mesh_fingerprint``,
``grid_fingerprint``), and a cache hit is accepted only after those attributes
are checked against the mesh and source grid in hand.  A filename can now
collide where a hash could not -- a different mesh with the same nCells, two
0.1-degree grids offset from each other -- so the check is what keeps stale or
foreign geometry from being reused silently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np


class CacheIdentityError(RuntimeError):
    """A cached or supplied product does not belong to the current geometry."""


def grid_dims_tag(lat: np.ndarray, lon: np.ndarray) -> str:
    """Source-grid label used in cache filenames, e.g. ``1800x3600``."""
    return f"{np.asarray(lat).size}x{np.asarray(lon).size}"


def resolve_grid_name(grid_name: str | None, n_cells: int) -> str:
    """MPAS grid label, e.g. ``x1.163842``.

    Falls back to ``x1.{nCells}`` so a hand-run CLI without ``--grid-name``
    still produces the uniform-mesh spelling the workflow would have passed.
    """
    return grid_name or f"x1.{n_cells}"


def scrip_file(cache_dir: Path, grid_name: str) -> Path:
    return Path(cache_dir) / f"mpas_scrip_{grid_name}.nc"


def neighbors_file(cache_dir: Path, grid_name: str) -> Path:
    return Path(cache_dir) / f"mpas_neighbors_{grid_name}.npz"


def gridspec_file(cache_dir: Path, dims_tag: str) -> Path:
    return Path(cache_dir) / f"gridspec_{dims_tag}.nc"


def weights_file(cache_dir: Path, dims_tag: str, grid_name: str) -> Path:
    return Path(cache_dir) / f"weights_{dims_tag}_to_{grid_name}.nc"


def read_nc_attrs(path: str | Path, names) -> dict[str, str | None]:
    """Read global NetCDF attributes, returning ``None`` for absent ones."""
    path = Path(path)
    names = list(names)
    raw: dict[str, object] = {}
    try:
        from netCDF4 import Dataset  # type: ignore
    except ImportError:
        Dataset = None

    if Dataset is not None:
        with Dataset(path) as ds:
            for n in names:
                if n in ds.ncattrs():
                    raw[n] = ds.getncattr(n)
    else:
        from scipy.io import netcdf_file
        with netcdf_file(path, "r", mmap=False) as ds:
            for n in names:
                if hasattr(ds, n):
                    raw[n] = getattr(ds, n)

    out: dict[str, str | None] = {}
    for n in names:
        v = raw.get(n)
        if isinstance(v, bytes):
            v = v.decode()
        out[n] = None if v is None else str(v).strip()
    return out


def stamp_nc_attrs(path: str | Path, **attrs: str) -> bool:
    """Append global attributes to an existing NetCDF file.

    ESMF writes weight files itself, so the provenance attributes have to be
    added afterwards.  Returns False when no append-capable backend is present;
    the caller keeps the product, and it validates later as unverifiable rather
    than as mismatched.
    """
    try:
        from netCDF4 import Dataset  # type: ignore
    except ImportError:
        return False
    with Dataset(path, "a") as ds:
        for k, v in attrs.items():
            ds.setncattr(k, str(v))
    return True


def _compare(path: Path, expected: Mapping[str, str]) -> list[str]:
    """Return a list of human-readable mismatches; empty means consistent."""
    found = read_nc_attrs(path, expected.keys())
    return [
        f"{k}: file has {found[k]!r}, current geometry is {v!r}"
        for k, v in expected.items()
        if found[k] is not None and found[k] != str(v)
    ]


def cache_is_usable(path: str | Path, expected: Mapping[str, str], *, label: str = "cached product") -> bool:
    """True when a cache entry may be reused for the current geometry.

    False for an absent or mismatched file, so the caller regenerates it.  A
    file that carries none of the expected attributes predates the stamping (or
    was written without an append-capable NetCDF backend) and is reused with a
    warning rather than silently discarded.
    """
    path = Path(path)
    if not path.exists():
        return False
    problems = _compare(path, expected)
    if problems:
        print(f"WARNING {label} {path.name} does not match the current geometry; regenerating")
        for p in problems:
            print(f"         {p}")
        return False
    return True


def require_cache_identity(path: str | Path, expected: Mapping[str, str], *, label: str = "weight file") -> None:
    """Validate an explicitly supplied product, raising on mismatch.

    Used for files named by configuration rather than found in the cache.  A
    mismatch is fatal: the run asked for this exact file, so quietly building a
    different one would ignore the request, and using it would remap every
    field through a matrix that belongs to another grid or mesh.
    """
    path = Path(path)
    problems = _compare(path, expected)
    if problems:
        detail = "; ".join(problems)
        raise CacheIdentityError(f"supplied {label} {path} does not match the current geometry: {detail}")
    found = read_nc_attrs(path, expected.keys())
    if all(v is None for v in found.values()):
        print(
            f"WARNING supplied {label} {path.name} carries no provenance attributes "
            f"({', '.join(expected)}); its source grid and mesh cannot be verified"
        )
