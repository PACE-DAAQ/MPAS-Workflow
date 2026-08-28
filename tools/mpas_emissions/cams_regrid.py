"""Workflow-native CAMS -> MPAS conservative emissions processing.

This module keeps the core conservative-remapping lineage attributable to
Duseong Jo's 2021 ESMF utility work in a small, mesh-native MPAS-Workflow
implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import glob
import os
import json

import numpy as np
import yaml

from .cams_source import CamsSource
from .mesh import MpasMesh
from .regular_grid import regular_grid_fingerprint, regular_cell_areas_sr, write_gridspec_from_centers
from .scrip import write_mpas_scrip
from .sparse_weights import SparseWeights
from .esmf_weights import generate_conservative_weights
from .io import write_mpas_emissions
from .scaling import scaling_factor, describe_scaling


ANTH_PREFIX = {
    "black-carbon": "bc",
    "organic-carbon": "oc",
    "ammonia": "nh3",
    "carbon-monoxide": "co",
    "sulphur-dioxide": "so2",
    "sulfur-dioxide": "so2",
    "isoprene": "iso",
    "monoterpenes": "mnt",
}
BIOG_VAR = {
    "alpha-pinene": "mnta_biog_megan",
    "beta-pinene": "mntb_biog_megan",
    "carbon-monoxide": "co_biog_megan",
    "isoprene": "iso_biog_megan",
    "other-monoterpenes": "mnt_biog_megan",
}


def _species_mapping(items) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if isinstance(item, dict):
            out.update({str(k): str(v) for k, v in item.items()})
        elif isinstance(item, str) and ":" in item:
            k, v = item.split(":", 1); out[k.strip()] = v.strip()
        else:
            out[str(item)] = str(item)
    return out


def _months(year: int, nt: int) -> list[datetime]:
    if nt == 12:
        return [datetime(year, m, 1) for m in range(1, 13)]
    # Explicitly avoid inventing higher-frequency valid times.  The current CAMS
    # anthropogenic/biogenic workflow is monthly; non-monthly sources must supply
    # a future decoder before use.
    raise ValueError(f"CAMS workflow currently expects 12 monthly records; found {nt}")


def _source_candidates(pattern: str, year: int, species_token: str) -> list[str]:
    p = pattern.format(year=year).replace("SPC", species_token)
    return sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else ([p] if os.path.exists(p) else [])


def _safe_output_file(output_dir: Path, mesh: MpasMesh, year: int, kind: str, long_name: str, grid_name: str) -> Path:
    canonical = {"sulphur-dioxide": "sulfur-dioxide"}.get(long_name, long_name)
    return output_dir / f"{grid_name}-{year}-{kind}_{canonical}.MPAS.nc"


def _write_mpas_monthly(
    out_file: Path,
    fields: dict[str, np.ndarray],
    *,
    times: list[datetime],
    n_cells: int,
    attrs: dict[str, object],
) -> Path:
    """Write monthly MPAS emissions as CDF-5 for SMIOL/PnetCDF."""
    return write_mpas_emissions(
        out_file, fields, times, n_cells=n_cells, attrs=attrs, unlimited_time=True
    )


@dataclass
class CamsProcessor:
    mesh: MpasMesh
    cache_dir: Path
    output_dir: Path
    provided_weight_file: Path | None = None
    chunk_links: int = 2_000_000
    global_conservation_tolerance: float = 5.0e-5
    grid_name: str | None = None
    _source_area_sr: np.ndarray | None = field(default=None, init=False, repr=False)
    _dst_area_sr: np.ndarray | None = field(default=None, init=False, repr=False)

    def _geometry(self, source_file: Path) -> tuple[Path, Path, Path, SparseWeights, str]:
        with CamsSource(source_file) as src:
            lat = src.lat; lon = src.lon
        if lat.ndim != 1 or lon.ndim != 1 or not np.all(np.diff(lat) > 0) or not np.all(np.diff(lon) > 0):
            raise ValueError(f"CAMS source coordinates in {source_file} must be 1-D and increasing")
        source_tag = regular_grid_fingerprint(lat, lon)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        src_grid = self.cache_dir / f"cams_gridspec_{lat.size}x{lon.size}_{source_tag}.nc"
        dst_grid = self.cache_dir / f"mpas_scrip_x{self.mesh.n_cells}_{self.mesh.fingerprint}.nc"
        weight = self.cache_dir / f"weights_src_{source_tag}_to_mpas_{self.mesh.fingerprint}_conserve.nc"
        if not src_grid.exists():
            write_gridspec_from_centers(lat, lon, src_grid)
        if not dst_grid.exists():
            write_mpas_scrip(self.mesh, dst_grid)

        if self.provided_weight_file and self.provided_weight_file.exists():
            use_weight = self.provided_weight_file
        else:
            use_weight = weight
            if not use_weight.exists():
                generate_conservative_weights(
                    src_grid, dst_grid, use_weight, dst_regional=not self.mesh.is_global
                )

        weights = SparseWeights.open(
            use_weight,
            n_dest=self.mesh.n_cells,
            n_src=lat.size * lon.size,
            require_full_destination=True,
            require_full_source=self.mesh.is_global,
        )
        return src_grid, dst_grid, use_weight, weights, source_tag

    def _check_global_conservation(self, src_field, dst_field, lat, lon, *, label: str) -> None:
        if not self.mesh.is_global:
            return
        if self._source_area_sr is None:
            self._source_area_sr = regular_cell_areas_sr(lat, lon)
        if self._dst_area_sr is None:
            self._dst_area_sr = self.mesh.area_cell / (self.mesh.sphere_radius_m ** 2)
        src_area = self._source_area_sr
        dst_area = self._dst_area_sr
        src_int = float(np.sum(np.asarray(src_field, dtype=np.float64) * src_area, dtype=np.float64))
        dst_int = float(np.sum(np.asarray(dst_field, dtype=np.float64) * dst_area, dtype=np.float64))
        scale = max(abs(src_int), 1.0e-300)
        rel = abs(dst_int - src_int) / scale
        if rel > self.global_conservation_tolerance:
            raise ValueError(
                f"CAMS conservative-remap check failed for {label}: "
                f"relative integral error={rel:.3e} > {self.global_conservation_tolerance:.3e}"
            )

    def process_config(self, config_file: str | Path, *, kind: str, reuse_existing: bool = True, year_override: int | None = None) -> list[Path]:
        cfg = yaml.safe_load(Path(config_file).read_text())
        year = int(year_override if year_override is not None else cfg["year"])
        mapping = _species_mapping(cfg.get("species", []))
        if not mapping:
            raise ValueError(f"{config_file}: species mapping is empty")
        pattern = str(cfg["inp_file_format"])
        sectors_cfg = [str(x) for x in cfg.get("sectors", [])]
        exclusions = [str(x) for x in cfg.get("sector_exclude", [])]
        outputs: list[Path] = []

        # One species is enough to establish geometry; all configured species
        # are required to share it.
        first_long, first_token = next(iter(mapping.items()))
        first_files = _source_candidates(pattern, year, first_long)
        if not first_files:
            # Some CAMS file templates use the short token rather than long name.
            first_files = _source_candidates(pattern, year, first_token)
        if not first_files:
            raise FileNotFoundError(f"No CAMS input for {first_long!r} from {pattern}")
        _, _, use_weight, weights, source_tag = self._geometry(Path(first_files[0]))

        reference_lat = reference_lon = None
        for long_name, token in mapping.items():
            grid_name = self.grid_name or f"x1.{self.mesh.n_cells}"
            out = _safe_output_file(self.output_dir, self.mesh, year, kind, long_name, grid_name)
            if out.is_symlink():
                out.unlink()
            elif reuse_existing and out.exists():
                outputs.append(out); continue

            candidates = _source_candidates(pattern, year, long_name)
            if not candidates:
                candidates = _source_candidates(pattern, year, token)
            if len(candidates) != 1:
                raise FileNotFoundError(
                    f"Expected exactly one CAMS input for {long_name!r}; found {candidates}"
                )
            src_path = Path(candidates[0])
            with CamsSource(src_path) as src:
                lat = src.lat; lon = src.lon
                if reference_lat is None:
                    reference_lat = lat.copy(); reference_lon = lon.copy()
                if not np.array_equal(lat, reference_lat) or not np.array_equal(lon, reference_lon):
                    raise ValueError(f"CAMS source grid changed between species: {src_path}")
                if regular_grid_fingerprint(lat, lon) != source_tag:
                    raise ValueError(f"CAMS source grid fingerprint changed: {src_path}")
                nt = src.nt
                times = _months(year, nt)
                available = src.data_variables()

                if kind == "anth":
                    selected = sectors_cfg[:] if sectors_cfg else available
                    selected = [s for s in selected if s in available]
                    if not selected:
                        raise ValueError(f"No requested CAMS sectors in {src_path}; available={available}")
                    prefix = ANTH_PREFIX.get(long_name, token)
                    out_fields = {f"{prefix}_anth_{sec}": np.zeros((nt, self.mesh.n_cells), np.float32) for sec in selected}
                    scale_block = dict(cfg.get("scaling", {}) or {})
                    sector_scale_cfg = {"scaling": {"sectors": scale_block.get("sectors", {})}}
                    scaled_sector_names = list(dict(scale_block.get("sectors", {})).keys())
                    for ti in range(nt):
                        exclusion_slices = {
                            sec: np.asarray(src.read(sec, ti), dtype=np.float64)
                            for sec in exclusions if src.has(sec)
                        }
                        # For a scaled CAMS sector, adjust the provided SUM by the
                        # sector anomaly so SUM remains consistent with its component
                        # fields.  This also makes NH3 sensitivity scaling explicit.
                        scaled_sector_slices = {
                            sec: np.asarray(src.read(sec, ti), dtype=np.float64)
                            for sec in scaled_sector_names if sec not in exclusions and src.has(sec)
                        }
                        for sec in selected:
                            field_name = f"{prefix}_anth_{sec}"
                            a = np.asarray(src.read(sec, ti), dtype=np.float64)
                            if a.shape != (lat.size, lon.size):
                                raise ValueError(f"{src_path}:{sec} unexpected shape {a.shape}")
                            if not np.all(np.isfinite(a)):
                                raise ValueError(f"{src_path}:{sec} contains NaN/Inf values")
                            if sec == "sum":
                                for x in exclusion_slices.values():
                                    a = a - x
                                for sname, x in scaled_sector_slices.items():
                                    sf = scaling_factor(sector_scale_cfg, sector=sname)
                                    a = a + (sf - 1.0) * x
                                amin = float(np.nanmin(a))
                                if amin < -1.0e-12:
                                    raise ValueError(
                                        f"{src_path}: exclusion/scaling makes sum negative ({amin})"
                                    )
                                a = np.maximum(a, 0.0)
                                a *= scaling_factor(cfg, species_aliases=[long_name, token, prefix], field=field_name)
                            elif sec in exclusions:
                                a = np.zeros_like(a)
                            else:
                                a *= scaling_factor(
                                    cfg, species_aliases=[long_name, token, prefix], field=field_name, sector=sec
                                )
                            dst_values = weights.apply(a.reshape(-1, order="C"), chunk_links=self.chunk_links)
                            self._check_global_conservation(
                                a, dst_values, lat, lon, label=f"{long_name}:{sec}:t={ti}"
                            )
                            out_fields[field_name][ti] = dst_values.astype(np.float32)
                elif kind == "biog":
                    # Biogenic source files normally contain one physical field.
                    # If a config explicitly lists sectors/fields, honor that;
                    # otherwise require an unambiguous single data variable.
                    selected = sectors_cfg[:] if sectors_cfg else available
                    selected = [s for s in selected if s in available]
                    if len(selected) != 1:
                        raise ValueError(
                            f"Biogenic source {src_path} must resolve to one field; selected={selected}, available={available}"
                        )
                    out_name = BIOG_VAR.get(long_name, f"{token}_biog_megan")
                    out_fields = {out_name: np.zeros((nt, self.mesh.n_cells), np.float32)}
                    sec = selected[0]
                    for ti in range(nt):
                        a = np.asarray(src.read(sec, ti), dtype=np.float64)
                        if not np.all(np.isfinite(a)):
                            raise ValueError(f"{src_path}:{sec} contains NaN/Inf values")
                        a *= scaling_factor(cfg, species_aliases=[long_name, token], field=out_name, sector=sec)
                        dst_values = weights.apply(a.reshape(-1, order="C"), chunk_links=self.chunk_links)
                        self._check_global_conservation(
                            a, dst_values, lat, lon, label=f"{long_name}:{sec}:t={ti}"
                        )
                        out_fields[out_name][ti] = dst_values.astype(np.float32)
                else:
                    raise ValueError("kind must be 'anth' or 'biog'")

            _write_mpas_monthly(
                out,
                out_fields,
                times=times,
                n_cells=self.mesh.n_cells,
                attrs={
                    "inventory": f"CAMS {kind}",
                    "source_file": str(src_path),
                    "source_grid_fingerprint": source_tag,
                    "mesh_fingerprint": self.mesh.fingerprint,
                    "weight_file": str(use_weight),
                    "regrid_method": "ESMF conservative sparse weights",
                    "attribution": "Original ESMF emissions-regridding methodology and utility lineage: Duseong Jo (2021)",
                    "sector_exclude": ",".join(exclusions),
                    "emissions_scaling": json.dumps(describe_scaling(cfg), sort_keys=True),
                },
            )
            outputs.append(out)
        return outputs
