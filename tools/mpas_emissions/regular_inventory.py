"""Config-driven regular-grid inventory -> MPAS processor.

This adapter is intentionally inventory-agnostic.  CEDS, GFAS and QFED differ
mostly in filenames, variable names, units and time cadence; those details live
in YAML while geometry, conservative regridding, gap filling, CDF-5 output and
provenance are shared here.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import glob, json, os, re

import numpy as np
import yaml

from .mesh import MpasMesh
from .regular_grid import regular_grid_fingerprint, regular_cell_areas_sr, write_gridspec_from_centers
from .scrip import write_mpas_scrip
from .sparse_weights import SparseWeights
from .esmf_weights import generate_conservative_weights
from .stream_io import MpasEmissionStreamWriter
from .time_axis import SourceRecord, TimeBracket, make_schedule, parse_datetime, resolve_brackets, resolve_calendar_day_brackets
from .units import convert
from .scaling import scaling_factor, describe_scaling


def _decode_times(ds, time_name: str) -> list[datetime]:
    from netCDF4 import num2date
    v = ds.variables[time_name]
    units = getattr(v, "units", None)
    if units is None: raise ValueError(f"time variable {time_name!r} lacks units")
    cal = getattr(v, "calendar", "standard")
    # Non-standard calendars (notably CEDS ``365_day``) legitimately return
    # cftime objects.  We only need the civil Y/M/D/H/M/S labels for emissions
    # interpolation, so accept either Python datetime or cftime objects.
    vals = num2date(v[:], units=units, calendar=cal, only_use_cftime_datetimes=False)
    return [datetime(x.year, x.month, x.day, x.hour, x.minute, x.second) for x in np.atleast_1d(vals)]


def _expand_template(s: str, *, year: int) -> str:
    raw = os.path.expanduser(os.path.expandvars(str(s)))
    # Priority lists may begin with an optional environment-provided source.
    # If that variable is unset, skip the candidate cleanly and continue to the
    # next project/campaign source instead of treating ${VAR} as str.format.
    if re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", raw):
        return ""
    return raw.format(year=year)


def _source_files(cfg: dict, year: int, pattern_override=None) -> list[str]:
    """Resolve the first available original-source pattern.

    ``source.files`` may be a string or a priority-ordered list.  This lets a
    Derecho configuration point first at /gdex/data or campaign holdings and
    fall back to a user cache without changing the processing code.
    """
    raw = pattern_override if pattern_override is not None else cfg["source"]["files"]
    patterns = raw if isinstance(raw, list) else [raw]
    tried = []
    for item in patterns:
        pattern = _expand_template(item, year=year); tried.append(pattern or str(item))
        if not pattern:
            continue
        files = sorted(glob.glob(pattern)) if any(c in pattern for c in "*?[") else ([pattern] if os.path.exists(pattern) else [])
        if files: return files
    raise FileNotFoundError(f"no source files match any configured pattern: {tried}")


def _name_candidates(value, defaults):
    if value is None:
        return list(defaults)
    return [str(x) for x in value] if isinstance(value, (list, tuple)) else [str(value)]


def _coord_names(ds, cfg: dict):
    src = cfg.get("source", {})
    lat_candidates = _name_candidates(src.get("latitude"), ["latitude", "lat"])
    lon_candidates = _name_candidates(src.get("longitude"), ["longitude", "lon"])
    lat_name = next((x for x in lat_candidates if x in ds.variables), None)
    lon_name = next((x for x in lon_candidates if x in ds.variables), None)
    if lat_name is None or lon_name is None:
        raise KeyError(
            f"could not identify latitude/longitude variables; tried "
            f"lat={lat_candidates}, lon={lon_candidates}; available={list(ds.variables)[:40]}"
        )
    return lat_name, lon_name


def _read_lat_lon(path: str, cfg: dict):
    from netCDF4 import Dataset
    with Dataset(path) as ds:
        lat_name, lon_name = _coord_names(ds, cfg)
        lat = np.asarray(ds.variables[lat_name][:], dtype=np.float64).squeeze()
        lon = np.asarray(ds.variables[lon_name][:], dtype=np.float64).squeeze()
    if lat.ndim != 1 or lon.ndim != 1: raise ValueError("regular-grid latitude/longitude must be 1-D")
    # Normalize decreasing latitude by letting field reader flip it; longitude must be increasing.
    lat_flip = bool(np.all(np.diff(lat) < 0))
    if lat_flip: lat = lat[::-1]
    if not np.all(np.diff(lat) > 0): raise ValueError("latitude must be monotonic")
    if not np.all(np.diff(lon) > 0): raise ValueError("longitude must be increasing")
    return lat, lon, lat_flip


def discover_records(files: list[str], cfg: dict) -> list[SourceRecord]:
    from netCDF4 import Dataset
    time_name = cfg["source"].get("time", "time")
    records: list[SourceRecord] = []
    for path in files:
        with Dataset(path) as ds:
            if time_name not in ds.variables:
                # Optional filename-time parsing for one-record files.
                fmt = cfg["source"].get("filename time format")
                if not fmt: raise ValueError(f"{path}: no {time_name!r} variable and no filename time format")
                import re
                rx = cfg["source"].get("filename time regex")
                if not rx: raise ValueError("filename time regex required when time variable is absent")
                m = re.search(rx, os.path.basename(path))
                if not m: raise ValueError(f"cannot parse time from {path}")
                records.append(SourceRecord(datetime.strptime(m.group(1), fmt), path, 0))
            else:
                for i, when in enumerate(_decode_times(ds, time_name)):
                    records.append(SourceRecord(when, path, i))
    return sorted(records, key=lambda r: r.valid_time)


def _read_field(record: SourceRecord, field_cfg: dict, cfg: dict, *, lat_flip: bool) -> np.ndarray:
    from netCDF4 import Dataset
    time_name = cfg["source"].get("time", "time")
    terms = field_cfg.get("sources")
    if terms is None:
        aliases = field_cfg.get("source variable aliases")
        if aliases is not None:
            terms = [{"variable aliases": aliases, "factor": 1.0}]
        else:
            terms = [{"variable": field_cfg["source variable"], "factor": 1.0}]
    total = None
    with Dataset(record.path) as ds:
        lat_name, lon_name = _coord_names(ds, cfg)
        for term in terms:
            factor = float(term.get("factor", 1.0))
            raw_names = term.get("variable aliases", term.get("variable"))
            candidates = _name_candidates(raw_names, [])
            name = next((x for x in candidates if x in ds.variables), None)
            if name is None:
                raise KeyError(
                    f"{record.path}: none of source variable aliases {candidates} exist; "
                    f"available={list(ds.variables)[:80]}"
                )
            v = ds.variables[name]
            dims = tuple(v.dimensions)
            sl = [slice(None)] * v.ndim
            kept_dims = list(dims)
            if time_name in dims:
                ti = dims.index(time_name); sl[ti] = record.time_index; kept_dims.pop(ti)
            raw = v[tuple(sl)]
            a = np.asarray(np.ma.filled(raw, np.nan), dtype=np.float64).squeeze()
            kept_dims = [d for d in kept_dims if d not in {""}]
            if a.ndim != 2: raise ValueError(f"{record.path}:{name} must resolve to 2-D lat/lon, got {a.shape}")
            if lat_name not in kept_dims or lon_name not in kept_dims:
                raise ValueError(f"{record.path}:{name} dimensions {kept_dims} do not contain {lat_name}/{lon_name}")
            # Reorder source arrays to canonical (lat,lon), independent of file order.
            lat_ax = kept_dims.index(lat_name); lon_ax = kept_dims.index(lon_name)
            a = np.moveaxis(a, (lat_ax, lon_ax), (0, 1))
            missing_policy = str(field_cfg.get("missing value policy", "error")).lower()
            if np.any(~np.isfinite(a)):
                if missing_policy == "zero": a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
                else: raise ValueError(f"{record.path}:{name} contains missing/NaN/Inf values")
            if lat_flip: a = a[::-1, :]
            total = factor * a if total is None else total + factor * a
    assert total is not None
    return convert(
        total,
        source_units=str(field_cfg.get("source units", "")),
        target_units=str(field_cfg.get("target units", field_cfg.get("source units", ""))),
        molecular_weight_g_mol=field_cfg.get("molecular weight g mol-1"),
        scale=float(field_cfg.get("scale", 1.0)),
    )


def _output_path(template: str, *, output_dir: Path, year: int, mesh: MpasMesh, grid_name: str) -> Path:
    name = str(template).format(year=year, nCells=mesh.n_cells, grid=grid_name)
    return output_dir / name


@dataclass
class RegularInventoryProcessor:
    mesh: MpasMesh
    cache_dir: Path
    output_dir: Path
    chunk_links: int = 2_000_000
    conservation_tolerance: float = 5.0e-5
    provided_weight_file: Path | None = None
    grid_name: str | None = None

    def _weights(self, source_file: str, cfg: dict):
        lat, lon, lat_flip = _read_lat_lon(source_file, cfg)
        tag = regular_grid_fingerprint(lat, lon)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        src_grid = self.cache_dir / f"regular_gridspec_{lat.size}x{lon.size}_{tag}.nc"
        dst_grid = self.cache_dir / f"mpas_scrip_x{self.mesh.n_cells}_{self.mesh.fingerprint}.nc"
        wf = self.cache_dir / f"weights_src_{tag}_to_mpas_{self.mesh.fingerprint}_conserve.nc"
        if not src_grid.exists(): write_gridspec_from_centers(lat, lon, src_grid)
        if not dst_grid.exists(): write_mpas_scrip(self.mesh, dst_grid)
        use = self.provided_weight_file if self.provided_weight_file and self.provided_weight_file.exists() else wf
        if not use.exists(): generate_conservative_weights(src_grid, dst_grid, use, dst_regional=not self.mesh.is_global)
        w = SparseWeights.open(use, n_dest=self.mesh.n_cells, n_src=lat.size*lon.size,
                               require_full_destination=True, require_full_source=self.mesh.is_global)
        return lat, lon, lat_flip, tag, Path(use), w

    def _maybe_fetch_native_source(self, cfg: dict, year: int) -> None:
        fetch = cfg.get("source", {}).get("fetch") or {}
        if not bool(fetch.get("enabled", False)):
            return
        provider = str(fetch.get("provider", "")).lower()
        if provider not in {"cams_ads_gfas", "gfas_ads"}:
            raise ValueError(f"unsupported source.fetch provider {provider!r}")
        # Fetch only the requested processing interval, chunked monthly.
        time_cfg = cfg.get("time", {})
        start = parse_datetime(str(time_cfg.get("start", f"{year}-01-01T00:00:00")).format(year=year))
        end = parse_datetime(str(time_cfg.get("end", f"{year}-12-31T23:59:59")).format(year=year))
        raw_dir = Path(str(fetch.get("cache directory", self.cache_dir / "raw" / "gfas")).format(
            year=year, cache_dir=self.cache_dir
        ))
        from .gfas_ads import fetch_gfas_ads
        files = fetch_gfas_ads(raw_dir, start=start, end=end, overwrite=bool(fetch.get("overwrite", False)))
        if not files:
            raise RuntimeError("GFAS ADS retrieval returned no files")
        # Prepend the deterministic cache glob to the normal source priority list.
        pattern = str(raw_dir / "GFAS_v1.2_native_0.1deg_*.nc")
        current = cfg["source"].get("files", [])
        current = current if isinstance(current, list) else [current]
        cfg["source"]["files"] = [pattern] + current

    def process(self, config_file: str | Path, *, year_override: int | None = None, reuse_existing: bool = True) -> list[Path]:
        cfg = yaml.safe_load(Path(config_file).read_text())
        year = int(year_override if year_override is not None else cfg["year"])
        # Optional source acquisition is used for native GFAS from the ADS.
        # Existing local source files remain the normal path when fetch.enabled=false.
        if bool((cfg.get("source", {}).get("fetch") or {}).get("enabled", False)):
            try:
                _source_files(cfg, year)
            except FileNotFoundError:
                self._maybe_fetch_native_source(cfg, year)
        outputs_cfg = cfg.get("outputs", [])
        if not outputs_cfg: raise ValueError(f"{config_file}: outputs is empty")

        # Establish geometry from the first output's source set.  Products may
        # override ``source files`` (useful when each CEDS/QFED/GFAS species is
        # stored separately) but every product must share the same grid.
        first_pattern = outputs_cfg[0].get("source files")
        first_files = _source_files(cfg, year, first_pattern)
        lat, lon, lat_flip, source_tag, weight_file, weights = self._weights(first_files[0], cfg)
        source_area = regular_cell_areas_sr(lat, lon) if self.mesh.is_global else None
        dest_area = self.mesh.area_cell/(self.mesh.sphere_radius_m**2) if self.mesh.is_global else None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []

        base_time_cfg = dict(cfg.get("time", {}))
        for product in outputs_cfg:
            product_files = _source_files(cfg, year, product.get("source files"))
            for f in product_files:
                la, lo, lf = _read_lat_lon(f, cfg)
                if lf != lat_flip or regular_grid_fingerprint(la, lo) != source_tag:
                    raise ValueError(f"source grid changes across files/products: {f}")
            records = discover_records(product_files, cfg)
            time_cfg = dict(base_time_cfg); time_cfg.update(product.get("time", {}))
            start = parse_datetime(str(time_cfg.get("start", f"{year}-01-01T00:00:00")).format(year=year))
            end = parse_datetime(str(time_cfg.get("end", f"{year}-12-31T23:00:00")).format(year=year))
            frequency = str(time_cfg.get("target frequency", "hourly")).lower()
            if frequency == "source":
                targets = [r.valid_time for r in records if start <= r.valid_time <= end]
            elif frequency == "monthly":
                targets=[]; t=datetime(start.year,start.month,1)
                while t <= end:
                    if t >= start: targets.append(t)
                    t = datetime(t.year + (1 if t.month == 12 else 0), 1 if t.month == 12 else t.month+1, 1)
            elif frequency == "daily":
                targets = make_schedule(start, end, timedelta(days=float(time_cfg.get("target step days", 1.0))))
            elif frequency == "hourly":
                targets = make_schedule(start, end, timedelta(hours=float(time_cfg.get("target step hours", 1.0))))
            else:
                raise ValueError(f"unsupported target frequency {frequency!r}")
            temporal_semantics = str(time_cfg.get("source temporal semantics", "instantaneous")).lower()
            if temporal_semantics in {"daily_mean", "daily mean", "daily"}:
                brackets = resolve_calendar_day_brackets(
                    records, targets,
                    missing_method=str(time_cfg.get("missing", "linear")),
                    max_gap_days=time_cfg.get("max interpolation gap days"),
                    allow_extrapolation=bool(time_cfg.get("allow extrapolation", False)),
                )
            else:
                max_gap_h = time_cfg.get("max interpolation gap hours")
                max_gap = None if max_gap_h in (None, "", 0) else timedelta(hours=float(max_gap_h))
                brackets = resolve_brackets(records, targets, method=str(time_cfg.get("missing", "linear")),
                                            max_gap=max_gap, allow_extrapolation=bool(time_cfg.get("allow extrapolation", False)))

            grid_name = self.grid_name or f"x1.{self.mesh.n_cells}"
            out = _output_path(product["file"], output_dir=self.output_dir, year=year, mesh=self.mesh, grid_name=grid_name)
            if out.is_symlink(): out.unlink()
            if reuse_existing and out.exists(): outputs.append(out); continue
            fields_cfg = product.get("fields", {})
            if not fields_cfg: raise ValueError(f"output {product.get('file')}: no fields")
            stats = {"exact": 0, "interpolated": 0, "held_or_nearest": 0, "largest_gap_hours": 0.0}
            cache: OrderedDict[tuple[SourceRecord,str], np.ndarray] = OrderedDict()

            def remapped(rec: SourceRecord, out_name: str, fcfg: dict) -> np.ndarray:
                key=(rec,out_name)
                if key in cache:
                    arr=cache.pop(key); cache[key]=arr; return arr
                src=_read_field(rec, fcfg, cfg, lat_flip=lat_flip)
                aliases = [out_name, out_name.split("_", 1)[0], fcfg.get("species", ""), fcfg.get("source variable", "")]
                src = src * scaling_factor(cfg, species_aliases=aliases, field=out_name)
                src = src * scaling_factor(product, species_aliases=aliases, field=out_name)
                if src.shape != (lat.size, lon.size): raise ValueError(f"{rec.path}:{out_name} shape {src.shape}")
                dst=weights.apply(src.reshape(-1, order="C"), chunk_links=self.chunk_links)
                if self.mesh.is_global:
                    si=float(np.sum(src*source_area)); di=float(np.sum(dst*dest_area)); rel=abs(di-si)/max(abs(si),1e-300)
                    if rel > self.conservation_tolerance:
                        raise ValueError(f"conservation failure {out_name} {rec.valid_time}: {rel:.3e}")
                cache[key]=dst
                while len(cache) > max(4, 2*len(fields_cfg)): cache.popitem(last=False)
                return dst

            attrs={
                "inventory": str(cfg.get("inventory", "regular-grid")),
                "source_grid_fingerprint": source_tag,
                "mesh_fingerprint": self.mesh.fingerprint,
                "weight_file": str(weight_file),
                "regrid_method": "ESMF conservative sparse weights",
                "time_missing_policy": str(time_cfg.get("missing", "linear")),
                "source_temporal_semantics": temporal_semantics,
                "emissions_scaling": json.dumps({"inventory": describe_scaling(cfg), "product": describe_scaling(product)}, sort_keys=True),
                "attribution": "Original ESMF emissions-regridding methodology and utility lineage: Duseong Jo (2021)",
            }
            with MpasEmissionStreamWriter(out, n_cells=self.mesh.n_cells, field_names=list(fields_cfg), attrs=attrs) as writer:
                for br in brackets:
                    vals={}
                    if br.exact: stats["exact"] += 1
                    elif br.before == br.after: stats["held_or_nearest"] += 1
                    else:
                        stats["interpolated"] += 1
                        stats["largest_gap_hours"] = max(stats["largest_gap_hours"], br.gap_seconds/3600.0)
                    for out_name, fcfg in fields_cfg.items():
                        a=remapped(br.before,out_name,fcfg)
                        if br.before == br.after: vals[out_name]=a
                        else:
                            b=remapped(br.after,out_name,fcfg)
                            vals[out_name]=(1.0-br.alpha_after)*a + br.alpha_after*b
                    writer.append(br.target, vals)
            sidecar=out.with_suffix(out.suffix+".provenance.json")
            sidecar.write_text(json.dumps({
                "inventory": cfg.get("inventory"), "year": year,
                "source_files": product_files, "target_count": len(targets), "source_record_count": len(records),
                "time_fill": stats, "source_grid_fingerprint": source_tag,
                "mesh_fingerprint": self.mesh.fingerprint, "weight_file": str(weight_file),
                "emissions_scaling": {"inventory": describe_scaling(cfg), "product": describe_scaling(product)},
            }, indent=2, default=str)+"\n")
            outputs.append(out)
        return outputs
