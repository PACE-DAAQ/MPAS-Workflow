#!/usr/bin/env python3
"""Validate an ESMF sparse conservative-regridding weight file.

The weight file is expected to contain ESMF's sparse triplets ``S``, ``row``
and ``col`` with 1-based indices.  Row ordering is *not* assumed to be globally
sorted; aggregation is done with numpy.bincount.

Optional MPAS mesh/SCRIP inputs add destination topology and conservation checks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np

try:
    from netCDF4 import Dataset
except ImportError as exc:  # CDF-5 weight files need a NetCDF-C capable reader
    raise SystemExit(
        "validate_weights.py requires the Python netCDF4 package (NetCDF-C backend)."
    ) from exc


def read_var(path: str | Path, name: str) -> np.ndarray:
    with Dataset(path) as ds:
        return np.asarray(ds.variables[name][:])


def summarize(weights: str | Path, *, n_dest: int | None = None,
              mesh: str | Path | None = None,
              scrip: str | Path | None = None) -> dict:
    with Dataset(weights) as ds:
        S = np.asarray(ds.variables["S"][:], dtype=np.float64)
        row = np.asarray(ds.variables["row"][:], dtype=np.int64)
        col = np.asarray(ds.variables["col"][:], dtype=np.int64)

    if not (S.size == row.size == col.size):
        raise ValueError("S, row, and col must have the same number of links")
    if S.size == 0:
        raise ValueError("weight file has no sparse links")
    if row.min() < 1 or col.min() < 1:
        raise ValueError("ESMF row/col indices are expected to be 1-based positive integers")

    inferred_dest = int(row.max())
    if n_dest is None:
        n_dest = inferred_dest
    if inferred_dest > n_dest:
        raise ValueError(f"row max {inferred_dest} exceeds n_dest={n_dest}")

    n_src = int(col.max())
    row_counts = np.bincount(row, minlength=n_dest + 1)[1:]
    row_sums = np.bincount(row, weights=S, minlength=n_dest + 1)[1:]
    col_counts = np.bincount(col, minlength=n_src + 1)[1:]

    reset_idx = np.where(row[1:] < row[:-1])[0]
    out = {
        "n_links": int(S.size),
        "n_dest": int(n_dest),
        "n_src_inferred": n_src,
        "row_min": int(row.min()),
        "row_max": int(row.max()),
        "col_min": int(col.min()),
        "col_max": int(col.max()),
        "missing_destination_rows": int(np.count_nonzero(row_counts == 0)),
        "missing_source_columns": int(np.count_nonzero(col_counts == 0)),
        "row_sum_min": float(row_sums.min()),
        "row_sum_max": float(row_sums.max()),
        "row_sum_max_abs_error_from_one": float(np.max(np.abs(row_sums - 1.0))),
        "links_per_destination": {
            "min": int(row_counts.min()),
            "median": float(np.median(row_counts)),
            "mean": float(row_counts.mean()),
            "p95": float(np.percentile(row_counts, 95)),
            "p99": float(np.percentile(row_counts, 99)),
            "max": int(row_counts.max()),
        },
        "destination_cells_per_source": {
            "min": int(col_counts.min()),
            "median": float(np.median(col_counts)),
            "mean": float(col_counts.mean()),
            "p95": float(np.percentile(col_counts, 95)),
            "p99": float(np.percentile(col_counts, 99)),
            "max": int(col_counts.max()),
        },
        "weights": {
            "min": float(S.min()),
            "median": float(np.median(S)),
            "mean": float(S.mean()),
            "p99": float(np.percentile(S, 99)),
            "max": float(S.max()),
            "negative": int(np.count_nonzero(S < 0.0)),
            "zero": int(np.count_nonzero(S == 0.0)),
            "nonfinite": int(np.count_nonzero(~np.isfinite(S))),
        },
        "row_order_resets": int(reset_idx.size),
        "row_order_reset_positions": [int(i + 1) for i in reset_idx[:20]],
    }

    if mesh is not None:
        n_edges = read_var(mesh, "nEdgesOnCell").astype(np.int64)
        lat = read_var(mesh, "latCell").astype(np.float64)
        lon = read_var(mesh, "lonCell").astype(np.float64)
        if n_edges.size != n_dest:
            raise ValueError(f"mesh nCells={n_edges.size} != n_dest={n_dest}")
        pent = np.where(n_edges == 5)[0]
        out["mpas_pentagons"] = [
            {
                "cell_1based": int(i + 1),
                "lat_deg": float(np.degrees(lat[i])),
                "lon_deg_0_360": float(np.degrees(lon[i]) % 360.0),
                "n_links": int(row_counts[i]),
                "row_sum": float(row_sums[i]),
            }
            for i in pent
        ]

    if scrip is not None:
        dst_area = read_var(scrip, "grid_area").astype(np.float64)
        if dst_area.size != n_dest:
            raise ValueError(f"SCRIP grid_size={dst_area.size} != n_dest={n_dest}")
        implied_src_area = np.bincount(
            col,
            weights=S * dst_area[row - 1],
            minlength=n_src + 1,
        )[1:]
        out["area_conservation"] = {
            "destination_area_sum_sr": float(dst_area.sum()),
            "implied_source_area_sum_sr": float(implied_src_area.sum()),
            "four_pi_sr": float(4.0 * np.pi),
            "global_area_difference_sr": float(implied_src_area.sum() - dst_area.sum()),
        }

    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("weights", help="ESMF sparse weight NetCDF file")
    p.add_argument("--n-dest", type=int, default=None)
    p.add_argument("--mesh", default=None, help="MPAS mesh/static file")
    p.add_argument("--scrip", default=None, help="destination SCRIP file")
    p.add_argument("--json", dest="json_path", default=None,
                   help="optional path for machine-readable JSON report")
    args = p.parse_args()

    report = summarize(args.weights, n_dest=args.n_dest, mesh=args.mesh, scrip=args.scrip)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.json_path:
        Path(args.json_path).write_text(text + "\n")


if __name__ == "__main__":
    main()
