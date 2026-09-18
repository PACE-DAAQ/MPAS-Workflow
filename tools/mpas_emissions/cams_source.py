"""CAMS regular-grid NetCDF access with lightweight backend fallbacks."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

import numpy as np

_COORD_NAMES = {"time", "lat", "lon", "lat_bnds", "lon_bnds", "bounds", "crs"}


class CamsSource(AbstractContextManager):
    """Read CAMS ``time,lat,lon`` files with netCDF4/h5py/scipy backends."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.backend = ""
        self.ds = None

        try:
            from netCDF4 import Dataset  # type: ignore
            self.ds = Dataset(self.path)
            self.backend = "netCDF4"
            return
        except ImportError:
            pass

        with self.path.open("rb") as f:
            magic = f.read(8)
        if magic.startswith(b"\x89HDF"):
            import h5py
            self.ds = h5py.File(self.path, "r")
            self.backend = "h5py"
            return
        if magic[:3] == b"CDF" and magic[3] in (1, 2):
            from scipy.io import netcdf_file
            self.ds = netcdf_file(self.path, "r", mmap=False)
            self.backend = "scipy"
            return
        raise RuntimeError(
            f"Cannot read CAMS file {self.path}: CDF-5 requires Python netCDF4/NetCDF-C"
        )

    def __exit__(self, exc_type, exc, tb):
        if self.ds is not None:
            self.ds.close()
        return False

    def _obj(self, name: str):
        if self.backend in ("netCDF4", "scipy"):
            return self.ds.variables[name]
        return self.ds[name]

    @property
    def lat(self) -> np.ndarray:
        return self.read("lat").astype(np.float64)

    @property
    def lon(self) -> np.ndarray:
        return self.read("lon").astype(np.float64)

    @property
    def time(self) -> np.ndarray:
        return self.read("time").astype(np.float64) if self.has("time") else np.asarray([], dtype=np.float64)

    @property
    def nlat(self) -> int:
        return int(self.lat.size)

    @property
    def nlon(self) -> int:
        return int(self.lon.size)

    @property
    def nt(self) -> int:
        if self.has("time"):
            return int(self.time.size)
        for name in self.data_variables():
            shape = self.shape(name)
            if len(shape) == 3:
                return int(shape[0])
        return 1

    def has(self, name: str) -> bool:
        if self.backend in ("netCDF4", "scipy"):
            return name in self.ds.variables
        return name in self.ds

    def variable_names(self) -> list[str]:
        if self.backend in ("netCDF4", "scipy"):
            return list(self.ds.variables.keys())
        return [name for name, obj in self.ds.items() if hasattr(obj, "shape")]

    def data_variables(self) -> list[str]:
        out = []
        nlat, nlon = self.nlat, self.nlon
        for name in self.variable_names():
            if name in _COORD_NAMES:
                continue
            shape = self.shape(name)
            if len(shape) in (2, 3) and shape[-2:] == (nlat, nlon):
                out.append(name)
        return out

    def shape(self, name: str) -> tuple[int, ...]:
        return tuple(int(v) for v in self._obj(name).shape)

    def read(self, name: str, key=None) -> np.ndarray:
        obj = self._obj(name)
        if self.backend == "scipy":
            raw = obj.data
            arr = raw.copy() if key is None else np.asarray(raw[key]).copy()
        else:
            arr = obj[:] if key is None else obj[key]
        if np.ma.isMaskedArray(arr):
            arr = np.ma.filled(arr, np.nan)
        return np.asarray(arr)

    def attrs(self, name: str) -> dict[str, object]:
        obj = self._obj(name)
        if self.backend == "netCDF4":
            return {k: obj.getncattr(k) for k in obj.ncattrs()}
        if self.backend == "h5py":
            return {str(k): v for k, v in obj.attrs.items()}
        return {str(k): v for k, v in getattr(obj, "_attributes", {}).items()}
