"""Common MPAS emissions NetCDF helpers.

MPAS-facing files are written as CDF-5 (``NETCDF3_64BIT_DATA``), not
NetCDF4/HDF5.  This is intentional for MPAS-v8+/SMIOL/PnetCDF workflows.

The Python package ``netCDF4`` is used only as a writer API; selecting
``format='NETCDF3_64BIT_DATA'`` produces a CDF-5 file in the NetCDF-3 family.
"""

from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np


CDF5_MAGIC = b"CDF\x05"
HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"


def encode_xtime(times: Sequence[datetime], strlen: int = 64) -> np.ndarray:
    """Encode datetimes as MPAS ``xtime(Time, StrLen)`` character data."""
    out = np.full((len(times), strlen), b"\x00", dtype="S1")
    for i, dt in enumerate(times):
        raw = dt.strftime("%Y-%m-%d_%H:%M:%S").encode("ascii")
        chars = np.frombuffer(raw[:strlen], dtype="S1")
        out[i, : chars.size] = chars
    return out


def netcdf_container_format(path: str | Path) -> str:
    """Identify the NetCDF container from its file signature.

    Returns one of ``cdf1``, ``cdf2``, ``cdf5``, ``hdf5`` or ``unknown``.
    This deliberately avoids depending on a NetCDF library so it can be used
    as a final guard after writing MPAS-facing files.
    """
    with open(path, "rb") as f:
        sig8 = f.read(8)
    if sig8.startswith(b"CDF\x01"):
        return "cdf1"
    if sig8.startswith(b"CDF\x02"):
        return "cdf2"
    if sig8.startswith(CDF5_MAGIC):
        return "cdf5"
    if sig8 == HDF5_MAGIC:
        return "hdf5"
    return "unknown"


def assert_mpas_compatible_file(path: str | Path, *, require_cdf5: bool = True) -> None:
    """Reject HDF5/NetCDF4 and, by default, require CDF-5."""
    fmt = netcdf_container_format(path)
    if fmt == "hdf5":
        raise ValueError(f"{path} is NetCDF4/HDF5; MPAS-facing emissions must not use HDF5")
    if require_cdf5 and fmt != "cdf5":
        raise ValueError(f"{path} has container {fmt}; expected CDF-5 (NETCDF3_64BIT_DATA)")
    if fmt not in {"cdf1", "cdf2", "cdf5"}:
        raise ValueError(f"{path} is not a recognized classic-family NetCDF file")


def write_mpas_emissions(
    out_file: str | Path,
    fields: Mapping[str, np.ndarray],
    times: Sequence[datetime],
    *,
    n_cells: int,
    attrs: Mapping[str, str | int | float] | None = None,
    strlen: int = 64,
    unlimited_time: bool = True,
) -> Path:
    """Write MPAS ``(Time,nCells)`` emissions as **CDF-5**.

    Notes
    -----
    ``netCDF4.Dataset`` is an API name.  The explicit
    ``format='NETCDF3_64BIT_DATA'`` argument below writes CDF-5 and does not
    create an HDF5/NetCDF4 file.
    """
    try:
        from netCDF4 import Dataset
    except ImportError as exc:  # pragma: no cover - depends on workflow environment
        raise RuntimeError(
            "Writing MPAS/SMIOL emissions requires the Python netCDF4 package "
            "with CDF-5 support (NETCDF3_64BIT_DATA)."
        ) from exc

    nt = len(times)
    prepared: dict[str, np.ndarray] = {}
    for name, arr in fields.items():
        arr = np.asarray(arr)
        if arr.shape == (n_cells,):
            arr = arr[None, :]
        if arr.shape != (nt, n_cells):
            raise ValueError(f"Field {name!r} has shape {arr.shape}; expected {(nt, n_cells)}")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"Field {name!r} contains NaN/Inf values")
        prepared[name] = np.asarray(arr, dtype=np.float32)

    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(str(out), "w", format="NETCDF3_64BIT_DATA") as ds:
        ds.createDimension("Time", None if unlimited_time else nt)
        ds.createDimension("nCells", n_cells)
        ds.createDimension("StrLen", strlen)

        xt = ds.createVariable("xtime", "S1", ("Time", "StrLen"))
        xt[:, :] = encode_xtime(times, strlen)
        xt.long_name = "model times"
        xt.calendar = "gregorian"
        xt.cell_methods = "string1: mean"

        for name, arr in prepared.items():
            var = ds.createVariable(name, "f4", ("Time", "nCells"))
            var[:, :] = arr

        for key, value in dict(attrs or {}).items():
            if key == "authors":
                continue
            ds.setncattr(key, value)
        ds.setncattr("mpas_io_container", "CDF-5 / NETCDF3_64BIT_DATA")

    # Fail immediately if a library/runtime silently produced some other format.
    assert_mpas_compatible_file(out, require_cdf5=True)
    return out
