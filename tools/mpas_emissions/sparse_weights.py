"""Sparse ESMF weight-file application and validation.

ESMF conservative weight files store 1-based sparse triplets ``S``, ``row``
and ``col``.  This module deliberately does *not* assume that links are sorted
by destination row; the validated CAMS->MPAS production file contains multiple
row-sorted blocks.

The direct sparse application is useful in MPAS-Workflow because generating
weights is expensive but applying an existing matrix is a simple linear
operation.  A link-chunked implementation keeps peak memory bounded for the
~14 million-link CAMS 0.1-degree -> x1.163842 production matrix.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


def _read_triplets(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read ESMF ``S,row,col`` with the best available NetCDF backend."""
    path = Path(path)
    try:
        from netCDF4 import Dataset  # type: ignore
    except ImportError:
        Dataset = None

    if Dataset is not None:
        with Dataset(path) as ds:
            S = np.asarray(ds.variables["S"][:], dtype=np.float64)
            row = np.asarray(ds.variables["row"][:], dtype=np.int32)
            col = np.asarray(ds.variables["col"][:], dtype=np.int32)
        return S, row, col

    # scipy handles classic/CDF-2 files and is enough for unit tests.  CDF-5
    # production weights require netCDF4/NetCDF-C (available in the intended
    # Derecho emissions environment).
    from scipy.io import netcdf_file
    try:
        with netcdf_file(path, "r", mmap=False) as ds:
            S = np.asarray(ds.variables["S"].data, dtype=np.float64).copy()
            row = np.asarray(ds.variables["row"].data, dtype=np.int32).copy()
            col = np.asarray(ds.variables["col"].data, dtype=np.int32).copy()
        return S, row, col
    except Exception as exc:
        raise RuntimeError(
            f"Cannot read ESMF weight file {path}. Install Python netCDF4 "
            "(NetCDF-C backend) for CDF-5 production weight files."
        ) from exc


@dataclass
class SparseWeights:
    """In-memory ESMF sparse mapping with 0-based row/column indices."""

    S: np.ndarray
    row0: np.ndarray
    col0: np.ndarray
    n_dest: int
    n_src: int
    source_path: str = ""

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        n_dest: int | None = None,
        n_src: int | None = None,
        require_full_destination: bool = True,
        require_full_source: bool = False,
        row_sum_tolerance: float = 5.0e-10,
    ) -> "SparseWeights":
        S, row, col = _read_triplets(path)
        if not (S.ndim == row.ndim == col.ndim == 1):
            raise ValueError("S, row, and col must be one-dimensional")
        if not (S.size == row.size == col.size) or S.size == 0:
            raise ValueError("S, row, and col must be non-empty and the same length")
        if not np.all(np.isfinite(S)):
            raise ValueError("ESMF weights contain NaN/Inf")
        if np.any(row < 1) or np.any(col < 1):
            raise ValueError("ESMF row/col indices must be positive 1-based integers")

        inferred_dest = int(row.max())
        inferred_src = int(col.max())
        nd = inferred_dest if n_dest is None else int(n_dest)
        ns = inferred_src if n_src is None else int(n_src)
        if inferred_dest > nd or inferred_src > ns:
            raise ValueError(
                f"weight dimensions exceed requested dimensions: "
                f"rows={inferred_dest}/{nd}, cols={inferred_src}/{ns}"
            )

        row0 = row.astype(np.int64, copy=False) - 1
        col0 = col.astype(np.int64, copy=False) - 1
        row_counts = np.bincount(row0, minlength=nd)
        col_counts = np.bincount(col0, minlength=ns)
        row_sums = np.bincount(row0, weights=S, minlength=nd)
        if require_full_destination and np.any(row_counts == 0):
            raise ValueError(f"weight file misses {np.count_nonzero(row_counts == 0)} destination cells")
        if require_full_source and np.any(col_counts == 0):
            raise ValueError(f"weight file misses {np.count_nonzero(col_counts == 0)} source cells")
        max_row_error = float(np.max(np.abs(row_sums[row_counts > 0] - 1.0)))
        if max_row_error > row_sum_tolerance:
            raise ValueError(
                f"destination row sums are not normalized: max |sum-1|={max_row_error:.3e}"
            )

        return cls(
            S=np.asarray(S, dtype=np.float64),
            row0=np.asarray(row0, dtype=np.int32),
            col0=np.asarray(col0, dtype=np.int32),
            n_dest=nd,
            n_src=ns,
            source_path=str(path),
        )

    @property
    def n_links(self) -> int:
        return int(self.S.size)

    def chunks(self, chunk_links: int = 2_000_000) -> Iterator[slice]:
        if chunk_links <= 0:
            raise ValueError("chunk_links must be positive")
        for start in range(0, self.n_links, chunk_links):
            yield slice(start, min(start + chunk_links, self.n_links))

    def apply(self, src_flat: np.ndarray, *, chunk_links: int = 2_000_000) -> np.ndarray:
        """Apply the sparse mapping to one source field.

        ``src_flat`` must use the same linear indexing as ESMF ``col``.  For
        CAMS ``(lat,lon)`` arrays this is NumPy C-order flattening, i.e. longitude
        varies fastest.
        """
        src = np.asarray(src_flat, dtype=np.float64).reshape(-1)
        if src.size != self.n_src:
            raise ValueError(f"source has {src.size} values; weight file expects {self.n_src}")
        dst = np.zeros(self.n_dest, dtype=np.float64)
        for sl in self.chunks(chunk_links):
            c = self.col0[sl].astype(np.int64, copy=False)
            r = self.row0[sl].astype(np.int64, copy=False)
            vals = self.S[sl] * src[c]
            dst += np.bincount(r, weights=vals, minlength=self.n_dest)
        return dst

    def destination_row_sums(self) -> np.ndarray:
        return np.bincount(self.row0, weights=self.S, minlength=self.n_dest)

    def destination_link_counts(self) -> np.ndarray:
        return np.bincount(self.row0, minlength=self.n_dest)
