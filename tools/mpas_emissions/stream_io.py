"""Streaming CDF-5 writer for large/high-frequency MPAS emission files."""
from __future__ import annotations
from pathlib import Path
from typing import Mapping
import os
import numpy as np

from .io import encode_xtime, assert_mpas_compatible_file


class MpasEmissionStreamWriter:
    def __init__(self, path, *, n_cells: int, field_names, attrs: Mapping[str, object] | None = None,
                 field_attrs: Mapping[str, Mapping[str, object]] | None = None, strlen: int = 64):
        try:
            from netCDF4 import Dataset
        except ImportError as exc:
            raise RuntimeError("Python netCDF4 with CDF-5 support is required") from exc
        self._Dataset = Dataset
        self.final = Path(path)
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = self.final.with_name(self.final.name + f".tmp.{os.getpid()}")
        if self.tmp.exists(): self.tmp.unlink()
        self.ds = Dataset(str(self.tmp), "w", format="NETCDF3_64BIT_DATA")
        self.ds.createDimension("Time", None)
        self.ds.createDimension("nCells", int(n_cells))
        self.ds.createDimension("StrLen", int(strlen))
        self.xtime = self.ds.createVariable("xtime", "S1", ("Time", "StrLen"))
        self.xtime.long_name = "model times"; self.xtime.calendar = "gregorian"
        self.vars = {str(n): self.ds.createVariable(str(n), "f4", ("Time", "nCells")) for n in field_names}
        for name, vatts in dict(field_attrs or {}).items():
            if name not in self.vars:
                continue
            for k, v in dict(vatts).items():
                self.vars[name].setncattr(str(k), v)
        self.n_cells = int(n_cells); self.strlen = int(strlen); self.index = 0
        for k, v in dict(attrs or {}).items():
            if k != "authors": self.ds.setncattr(str(k), v)
        self.ds.setncattr("mpas_io_container", "CDF-5 / NETCDF3_64BIT_DATA")

    def append(self, when, fields: Mapping[str, np.ndarray]):
        i = self.index
        self.xtime[i:i+1, :] = encode_xtime([when], self.strlen)
        for name, var in self.vars.items():
            if name not in fields: raise KeyError(f"missing output field {name!r}")
            a = np.asarray(fields[name], dtype=np.float32).reshape(-1)
            if a.size != self.n_cells: raise ValueError(f"{name}: expected {self.n_cells} cells, got {a.size}")
            if not np.all(np.isfinite(a)): raise ValueError(f"{name}: NaN/Inf")
            var[i, :] = a
        self.ds.sync(); self.index += 1

    def close(self, *, commit: bool = True):
        if self.ds is not None:
            self.ds.close(); self.ds = None
        if commit:
            assert_mpas_compatible_file(self.tmp, require_cdf5=True)
            os.replace(self.tmp, self.final)
            return self.final
        if self.tmp.exists(): self.tmp.unlink()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close(commit=exc_type is None)
