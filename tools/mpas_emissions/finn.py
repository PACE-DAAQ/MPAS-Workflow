"""FINN point-source aggregation on a native MPAS mesh."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from .mesh import MpasMesh

AVOGADRO = 6.022e23
SECONDS_PER_DAY = 86400.0
AEROSOL_MW_G_MOL = 12.0


@dataclass(frozen=True)
class MappingResult:
    """Result of mapping point records to MPAS cells."""

    cell_ids: np.ndarray
    distance_km: np.ndarray
    accepted: np.ndarray


def aggregate_points_to_mpas(
    mesh: MpasMesh,
    lat_deg: np.ndarray,
    lon_deg: np.ndarray,
    values: np.ndarray,
    *,
    statistic: str = "sum",
    interior_only: bool = False,
    reject_outside: bool = False,
    max_distance_factor: float = 2.5,
) -> tuple[np.ndarray, MappingResult]:
    """Aggregate point values onto MPAS cells without a Python point loop.

    ``statistic='sum'`` is suitable for FINN emission totals. ``'mean'`` is
    useful for scalar metadata such as injection-height diagnostics.
    """
    values = np.asarray(values, dtype=np.float64)
    lat = np.asarray(lat_deg, dtype=np.float64)
    lon = np.asarray(lon_deg, dtype=np.float64)
    if values.shape != lat.shape or values.shape != lon.shape:
        raise ValueError("lat, lon and values must have identical shapes")

    cell_ids, distance_km, accepted = mesh.nearest_cells(
        lat,
        lon,
        interior_only=interior_only,
        reject_outside=reject_outside,
        max_distance_factor=max_distance_factor,
    )
    finite_values = np.isfinite(values)
    use = accepted & finite_values & (cell_ids >= 0)

    out = np.zeros(mesh.n_cells, dtype=np.float64)
    if statistic == "sum":
        np.add.at(out, cell_ids[use], values[use])
    elif statistic == "mean":
        counts = np.zeros(mesh.n_cells, dtype=np.int64)
        np.add.at(out, cell_ids[use], values[use])
        np.add.at(counts, cell_ids[use], 1)
        nz = counts > 0
        out[nz] /= counts[nz]
    else:
        raise ValueError("statistic must be 'sum' or 'mean'")

    return out, MappingResult(cell_ids, distance_km, accepted)


def map_finn_dataframe(
    df: pd.DataFrame,
    mesh: MpasMesh,
    species_map: Mapping[str, str],
    species_type: Mapping[str, str],
    *,
    lat_column: str = "LATI",
    lon_column: str = "LONGI",
    interior_only: bool = False,
    reject_outside: bool = False,
    max_distance_factor: float = 2.5,
) -> tuple[dict[str, np.ndarray], MappingResult]:
    """Vectorized replacement for the legacy FINN nearest-cell loop."""
    if lat_column not in df or lon_column not in df:
        raise KeyError(f"FINN table must contain {lat_column!r} and {lon_column!r}")

    lat = pd.to_numeric(df[lat_column], errors="coerce").to_numpy(dtype=float)
    lon = pd.to_numeric(df[lon_column], errors="coerce").to_numpy(dtype=float)
    cell_ids, distance_km, accepted = mesh.nearest_cells(
        lat,
        lon,
        interior_only=interior_only,
        reject_outside=reject_outside,
        max_distance_factor=max_distance_factor,
    )
    mapping = MappingResult(cell_ids, distance_km, accepted)

    mapped: dict[str, np.ndarray] = {}
    for finn_name, mpas_name in species_map.items():
        if finn_name not in df:
            continue
        values = pd.to_numeric(df[finn_name], errors="coerce").to_numpy(dtype=float)
        use = accepted & np.isfinite(values) & (cell_ids >= 0)
        stype = species_type.get(finn_name, "gas").lower()

        total = np.zeros(mesh.n_cells, dtype=np.float64)
        if stype == "scalar":
            count = np.zeros(mesh.n_cells, dtype=np.int64)
            np.add.at(total, cell_ids[use], values[use])
            np.add.at(count, cell_ids[use], 1)
            nz = count > 0
            total[nz] /= count[nz]
        else:
            np.add.at(total, cell_ids[use], values[use])

        mapped[mpas_name] = total

    return mapped, mapping


def convert_finn_to_mpas_flux(
    values_per_cell: np.ndarray,
    area_cell_m2: np.ndarray,
    species_type: str,
) -> np.ndarray:
    """Apply FINN unit conversions for MPAS emissions."""
    values = np.asarray(values_per_cell, dtype=np.float64)
    area_cm2 = np.asarray(area_cell_m2, dtype=np.float64) * 1.0e4
    stype = species_type.lower()
    if stype == "aerosol":
        values_g = values * 1000.0
        values_mol_s = (values_g / AEROSOL_MW_G_MOL) / SECONDS_PER_DAY
        return values_mol_s * AVOGADRO / area_cm2
    if stype == "gas":
        values_mol_s = values / SECONDS_PER_DAY
        return values_mol_s * AVOGADRO / area_cm2
    if stype == "scalar":
        return values.copy()
    raise ValueError(f"Unsupported FINN species_type: {species_type!r}")


def apply_diurnal_profile(
    daily_cell_values: np.ndarray,
    local_hour_offset: np.ndarray,
    profile: np.ndarray,
) -> np.ndarray:
    """Expand daily cell values to hourly values using local-solar-time factors."""
    values = np.asarray(daily_cell_values, dtype=np.float64)
    offsets = np.asarray(local_hour_offset, dtype=np.int64)
    profile = np.asarray(profile, dtype=np.float64)
    if profile.shape != (24,):
        raise ValueError("profile must have exactly 24 hourly factors")
    if values.shape != offsets.shape:
        raise ValueError("daily_cell_values and local_hour_offset must have same shape")
    if not np.isfinite(profile).all() or profile.sum() <= 0:
        raise ValueError("profile must contain finite positive total weight")

    normalized = profile / profile.sum() * 24.0
    # shifted[hour, cell] = profile in local time, shifted to UTC for each cell.
    hours = np.arange(24)[:, None]
    idx = (hours + offsets[None, :]) % 24
    shifted = normalized[idx]
    return shifted * values[None, :]
