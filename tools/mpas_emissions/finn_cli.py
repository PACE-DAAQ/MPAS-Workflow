#!/usr/bin/env python3
"""Mesh-native FINN -> MPAS processor for MPAS-Workflow.

The primary FINN data set may contain missing *days*.  Short gaps can be
interpolated on the MPAS mesh, while long/consecutive gaps can be filled by an
explicit fallback inventory (for example FINNv1).  No fallback scaling is
applied by default: the February-2026 project diagnostics showed that a simple
overlap-derived FINNv1->FINNv2.5 scaling can be dominated by fire-event spikes.

All MPAS-facing outputs are streamed as CDF-5.  A separate daily PRM fire-
statistics file is written with the required fire-size mean and, when
available, the three optional lowbc diagnostics used by the current PRM
MPAS-GOCART2G plume-rise Registry: area mean/std and FRP mean/std.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .mesh import MpasMesh
from .finn import AVOGADRO, SECONDS_PER_DAY, AEROSOL_MW_G_MOL, apply_diurnal_profile
from .locking import file_lock
from .stream_io import MpasEmissionStreamWriter
from .scaling import scaling_factor, describe_scaling


def _date_from_name(path):
    """Parse YYYYMMDD or FINNv1 YYYYDOY dates from a filename/URL."""
    name = os.path.basename(str(path).split("?", 1)[0])
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if m:
        try:
            return datetime.strptime("".join(m.groups()), "%Y%m%d")
        except ValueError:
            pass
    # Legacy FINNv1 NRT files use GLOB_MOZ4_YYYYDDD.txt.gz.
    m = re.search(r"(?:GLOB_[A-Za-z0-9]+_)?(20\d{2})(\d{3})(?:\D|$)", name)
    if m:
        try:
            return datetime.strptime("".join(m.groups()), "%Y%j")
        except ValueError:
            pass
    return None


def _remote_daily_entries(src: dict, start: datetime, end: datetime) -> dict[datetime, str]:
    """Build lazy URL entries for a date-templated daily source.

    No network access occurs here.  Files are fetched only for dates that are
    actually used (e.g. consecutive FINNv2.5 gap days), keeping FINNv1 fallback
    traffic small.  Supported template keys: year, month, day, doy.
    """
    tmpl = src.get("url_template") or src.get("url template")
    if not tmpl:
        return {}
    found = {}
    dt = datetime(start.year, start.month, start.day)
    stop = datetime(end.year, end.month, end.day)
    while dt <= stop:
        if _source_enabled(src, dt.year):
            found[dt] = os.path.expandvars(str(tmpl)).format(
                year=dt.year, month=dt.month, day=dt.day, doy=dt.timetuple().tm_yday
            )
        dt += timedelta(days=1)
    return found


def _localize_source(path: str, cache_dir: Path, dt: datetime) -> str:
    """Return a local readable path, downloading an HTTPS source lazily."""
    path = str(path)
    if not re.match(r"^https?://", path, flags=re.I):
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    base = os.path.basename(path.split("?", 1)[0]) or f"FINN_{dt:%Y%m%d}.txt.gz"
    target = cache_dir / base
    if target.exists() and target.stat().st_size > 0:
        return str(target)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        req = urllib.request.Request(path, headers={"User-Agent": "MPAS-Workflow-emissions/1.5"})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        if tmp.stat().st_size == 0:
            raise IOError(f"empty download from {path}")
        os.replace(tmp, target)
    except Exception as exc:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise FileNotFoundError(
            f"unable to obtain FINN fallback for {dt:%Y-%m-%d} from {path}: {exc}"
        ) from exc
    return str(target)


def _clean_numeric(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed")].copy()
    for c in df.columns:
        if c in ("LATI", "LONGI"):
            continue
        s = df[c].astype(str).str.replace("D", "E", regex=False).str.replace("d", "E", regex=False)
        df[c] = pd.to_numeric(s, errors="coerce")
    return df


def _finn_separator(path: str) -> str:
    """Choose the delimiter from the actual FINN file content.

    Some historical FINNv2.5.1 NRT ``base_FRP`` files were distributed with
    malformed delimiters, but the 2024 project sample is a valid comma-separated
    file despite having the same filename family.  Therefore filename-based
    forcing is unsafe.  Inspect the first non-comment header and data row: use
    comma parsing when both contain a consistent comma field structure, and
    otherwise fall back to whitespace parsing.
    """
    name = os.path.basename(str(path).split("?", 1)[0])
    opener = gzip.open if name.endswith(".gz") else open
    try:
        rows = []
        with opener(path, "rt", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rows.append(line)
                if len(rows) >= 2:
                    break
        if rows:
            header_commas = rows[0].count(",")
            if header_commas >= 2:
                # A well-formed CSV data row should have nearly the same comma
                # count as its header.  If not, this is the malformed base_FRP
                # case for which whitespace parsing is the safer fallback.
                if len(rows) == 1 or rows[1].count(",") >= max(2, header_commas - 1):
                    return ","
                return r"[,\s]+"
            return r"[,\s]+"
    except (OSError, TypeError):
        pass
    return ","


def _read_finn_csv(path: str, **kwargs):
    """Read FINN text using content-detected delimiters and trimmed CSV fields."""
    kwargs.setdefault("skipinitialspace", True)
    sep = _finn_separator(path)
    if sep != ",":
        kwargs.setdefault("engine", "python")
    df = pd.read_csv(path, sep=sep, comment="#", **kwargs)
    # FINN headers may contain a space after a comma (e.g. `` PM10``/`` APIN``).
    # Normalize names once so species maps are independent of that formatting.
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_column(columns, aliases):
    lookup = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        hit = lookup.get(str(alias).strip().lower())
        if hit is not None:
            return hit
    return None


def _aggregate_prm_stats(
    df,
    mesh,
    *,
    interior_only=False,
    reject_outside=False,
    max_distance_factor=2.5,
    area_columns=("AREA", "FIRE_AREA", "area"),
    frp_columns=("FRP", "frp"),
    require_frp=False,
):
    """Aggregate FINN each-fire fire properties to MPAS PRM fields.

    Current PRM-author guidance (Aug 2026) makes only active-fire-size average
    mandatory.  AREA standard deviation and FRP average/std are optional.
    When AREA is present we compute both AREA mean and population std (ddof=0).
    When FRP is absent its two output arrays are zero-filled so workflow-
    generated files remain compatible with stream templates that expose all
    four lowbc fields.  ``require_frp`` is retained only as an opt-in scientific
    guard for external callers; the workflow itself does not use it.
    """
    df = _clean_numeric(df)
    lat_col = _find_column(df.columns, ("LATI", "LAT", "latitude"))
    lon_col = _find_column(df.columns, ("LONGI", "LON", "longitude"))
    area_col = _find_column(df.columns, area_columns)
    frp_col = _find_column(df.columns, frp_columns)
    if lat_col is None or lon_col is None:
        raise KeyError(f"FINN PRM source lacks latitude/longitude columns: {list(df.columns)}")
    if area_col is None:
        raise KeyError(f"FINN PRM source lacks AREA column; tried {list(area_columns)}")
    if frp_col is None and require_frp:
        raise KeyError(f"FINN PRM source lacks FRP column; tried {list(frp_columns)}")

    lat = pd.to_numeric(df[lat_col], errors="coerce").to_numpy(float)
    lon = pd.to_numeric(df[lon_col], errors="coerce").to_numpy(float)
    cell_ids, _, accepted = mesh.nearest_cells(
        lat, lon, interior_only=interior_only, reject_outside=reject_outside,
        max_distance_factor=max_distance_factor,
    )

    def moments(values):
        values = pd.to_numeric(values, errors="coerce").to_numpy(float)
        use = accepted & (cell_ids >= 0) & np.isfinite(values) & (values >= 0.0)
        ids = cell_ids[use]
        vv = values[use]
        count = np.bincount(ids, minlength=mesh.n_cells).astype(np.int64)
        sums = np.bincount(ids, weights=vv, minlength=mesh.n_cells).astype(np.float64)
        sumsq = np.bincount(ids, weights=vv * vv, minlength=mesh.n_cells).astype(np.float64)
        mean = np.zeros(mesh.n_cells, dtype=np.float64)
        std = np.zeros(mesh.n_cells, dtype=np.float64)
        nz = count > 0
        mean[nz] = sums[nz] / count[nz]
        var = np.zeros(mesh.n_cells, dtype=np.float64)
        var[nz] = np.maximum(sumsq[nz] / count[nz] - mean[nz] * mean[nz], 0.0)
        std[nz] = np.sqrt(var[nz])
        return mean, std

    area_avg, area_std = moments(df[area_col])
    if frp_col is None:
        frp_avg = np.zeros(mesh.n_cells, dtype=np.float64)
        frp_std = np.zeros(mesh.n_cells, dtype=np.float64)
    else:
        frp_avg, frp_std = moments(df[frp_col])
    return {
        "firesize_biob_modis_avg": area_avg,
        "firesize_biob_modis_std": area_std,
        "frp_biob_modis_avg": frp_avg,
        "frp_biob_modis_std": frp_std,
    }, int(np.count_nonzero(accepted)), len(df)


def _aggregate(
    df,
    mesh,
    species_map,
    species_type,
    interior_only,
    reject_outside,
    max_distance_factor,
    scaling_cfg=None,
):
    df = _clean_numeric(df)
    lat = pd.to_numeric(df["LATI"], errors="coerce").to_numpy(float)
    lon = pd.to_numeric(df["LONGI"], errors="coerce").to_numpy(float)
    cell_ids, _, accepted = mesh.nearest_cells(
        lat,
        lon,
        interior_only=interior_only,
        reject_outside=reject_outside,
        max_distance_factor=max_distance_factor,
    )
    out = {}
    for src, dst in species_map.items():
        if src not in df or dst == "co2_biob_modis":
            continue
        v = pd.to_numeric(df[src], errors="coerce").to_numpy(float)
        use = accepted & np.isfinite(v) & (cell_ids >= 0)
        st = species_type.get(src, "gas").lower()
        sums = np.bincount(cell_ids[use], weights=v[use], minlength=mesh.n_cells).astype(float)
        if st == "scalar":
            cnt = np.bincount(cell_ids[use], minlength=mesh.n_cells)
            mean = np.zeros(mesh.n_cells)
            nz = cnt > 0
            mean[nz] = sums[nz] / cnt[nz]
            out[dst] = mean
            continue
        if st == "aerosol":
            g = sums * 1000.0
            arr = (g / AEROSOL_MW_G_MOL / SECONDS_PER_DAY) * AVOGADRO / (mesh.area_cell * 1e4)
        elif st == "gas":
            arr = (sums / SECONDS_PER_DAY) * AVOGADRO / (mesh.area_cell * 1e4)
        else:
            arr = sums
        fac = scaling_factor(scaling_cfg, species_aliases=[src, dst], field=dst)
        out[dst] = arr * fac
    return out, int(np.count_nonzero(accepted)), len(df)


def _source_entries(cfg: dict | None) -> list[dict]:
    """Return source candidates in priority order.

    ``sources`` is new in v1.4 and permits a direct GDEX source followed by an
    NRT/local fallback.  Legacy single-source FINN YAML remains valid.
    """
    if not cfg:
        return []
    entries = cfg.get("sources")
    if entries:
        return [dict(x) for x in entries]
    return [dict(cfg)]


def _source_enabled(src: dict, year: int) -> bool:
    years = src.get("valid years")
    if years is not None:
        if isinstance(years, (list, tuple)) and len(years) == 2 and all(isinstance(x, (int, float)) for x in years):
            return int(years[0]) <= year <= int(years[1])
        return year in {int(x) for x in years}
    lo = int(src.get("start year", -999999)); hi = int(src.get("end year", 999999))
    return lo <= year <= hi


def _expand_source(s: str, *, year: int, month: int) -> str:
    raw = os.path.expanduser(os.path.expandvars(str(s)))
    # Optional local sources may reference an environment variable that is not
    # defined.  Treat such a candidate as unavailable rather than letting
    # Python ``str.format`` interpret ${VAR} as a formatting token.
    if re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", raw):
        return ""
    return raw.format(year=year, month=f"{month:02d}")


def _find_date_column(columns, cfg):
    preferred = cfg.get("annual_date_column", ["DAY", "DATE", "YYYYMMDD", "date", "day"])
    if isinstance(preferred, str): preferred = [preferred]
    lookup = {str(c).lower(): c for c in columns}
    for name in preferred:
        if str(name).lower() in lookup:
            return lookup[str(name).lower()]
    return None


def _parse_container_dates(series, path: str, cfg: dict):
    """Parse FINN dates in annual or monthly each-fire containers."""
    raw = series.astype(str).str.strip()
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in cfg.get("annual_date_formats", ["%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y%j"]):
        parsed = parsed.fillna(pd.to_datetime(raw, format=fmt, errors="coerce"))
    # Numeric DAY fields are common.  If values exceed 31 they are interpreted
    # as day-of-year.  Otherwise use YYYYMM from the container filename when
    # available (important for the 2022/2023 monthly GDEX files).
    nums = pd.to_numeric(series, errors="coerce")
    missing = parsed.isna() & nums.notna()
    if missing.any():
        vals = nums.loc[missing].astype(int)
        ym = re.search(r"(19\d{2}|20\d{2})(0[1-9]|1[0-2])", os.path.basename(path))
        yy = re.search(r"(19\d{2}|20\d{2})", os.path.basename(path))
        ref_year = int(cfg.get("annual_reference_year", yy.group(1) if yy else 0))
        mode = str(cfg.get("annual_day_mode", "auto")).lower()
        if mode == "auto": mode = "doy" if vals.max() > 31 else ("dom" if ym else "doy")
        if mode == "doy":
            x = vals.map(lambda d: f"{ref_year}{d:03d}")
            parsed.loc[missing] = pd.to_datetime(x, format="%Y%j", errors="coerce")
        elif mode == "dom":
            if not ym:
                raise ValueError(f"{path}: DAY looks like day-of-month but YYYYMM cannot be inferred")
            ref_year, ref_month = int(ym.group(1)), int(ym.group(2))
            x = vals.map(lambda d: f"{ref_year}{ref_month:02d}{d:02d}")
            parsed.loc[missing] = pd.to_datetime(x, format="%Y%m%d", errors="coerce")
        else:
            raise ValueError("annual_day_mode must be auto, doy, or dom")
    return parsed


def _container_files(src: dict, start: datetime, end: datetime) -> list[str]:
    out = []
    for year in range(start.year, end.year + 1):
        if not _source_enabled(src, year): continue
        for month in range(1, 13):
            first = datetime(year, month, 1)
            if first > end or (year == start.year and month < start.month): continue
            d = _expand_source(src["emis_dir"], year=year, month=month)
            q = _expand_source(src["emis_file_pattern"], year=year, month=month)
            if not d or not q:
                continue
            out.extend(glob.glob(os.path.join(d, q)))
    return sorted(set(out))


def _extract_container_days(src: dict, start: datetime, end: datetime, cache_dir: Path) -> dict[datetime, str]:
    """Extract requested days from annual/monthly GDEX FINN containers.

    This streams huge text files in chunks and caches only the requested days,
    avoiding a copy of the multi-GB original FINN file.
    """
    files = _container_files(src, start, end)
    if not files: return {}
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(src.get("name", "source")))
    daily_dir = cache_dir / name
    daily_dir.mkdir(parents=True, exist_ok=True)
    found: dict[datetime, str] = {}
    chunksize = int(src.get("annual_chunksize", 250000))
    for f in files:
        header = _read_finn_csv(f, nrows=0)
        header = header.loc[:, ~header.columns.astype(str).str.contains(r"^Unnamed")]
        date_col = _find_date_column(header.columns, src)
        if date_col is None:
            raise KeyError(f"{f}: no FINN date column found; set annual_date_column. Columns={list(header.columns)}")
        buckets: dict[datetime, list[pd.DataFrame]] = {}
        for chunk in _read_finn_csv(f, chunksize=chunksize):
            chunk = chunk.loc[:, ~chunk.columns.astype(str).str.contains(r"^Unnamed")]
            dates = _parse_container_dates(chunk[date_col], f, src)
            use = dates.notna() & (dates >= start) & (dates <= end + timedelta(days=1) - timedelta(seconds=1))
            if not use.any(): continue
            sub = chunk.loc[use].copy(); dd = dates.loc[use].dt.normalize()
            sub["__date__"] = dd.values
            for d, part in sub.groupby("__date__", sort=False):
                dt = pd.Timestamp(d).to_pydatetime()
                part = part.drop(columns=["__date__"])
                buckets.setdefault(dt, []).append(part)
        for dt, parts in buckets.items():
            target = daily_dir / f"FINN_{dt:%Y%m%d}.csv.gz"
            pd.concat(parts, ignore_index=True).to_csv(target, index=False, compression="gzip")
            if dt in found and os.path.realpath(found[dt]) != os.path.realpath(target):
                raise ValueError(f"duplicate FINN container records for {dt:%Y-%m-%d}")
            found[dt] = str(target)
    return found


def _discover_one_source(src: dict, start: datetime, end: datetime, cache_dir: Path) -> dict[datetime, str]:
    typ = str(src.get("file_type", "daily")).lower()
    if typ in {"annual", "container", "monthly"}:
        return _extract_container_days(src, start, end, cache_dir)
    if typ != "daily": raise ValueError(f"unsupported FINN file_type={typ!r}")
    # A date-templated HTTPS source is represented lazily; download happens
    # only if that day is ultimately selected by the gap-resolution policy.
    remote = _remote_daily_entries(src, start, end)
    found: dict[datetime, str] = {}
    for f in _container_files(src, start, end) if src.get("emis_dir") and src.get("emis_file_pattern") else []:
        dt = _date_from_name(f)
        if dt and start <= dt <= end:
            if dt in found and os.path.realpath(found[dt]) != os.path.realpath(f):
                raise ValueError(f"duplicate FINN file for {dt:%Y-%m-%d}: {found[dt]} and {f}")
            found[dt] = f
    # Local/project holdings take priority over HTTPS.
    for dt, url in remote.items():
        found.setdefault(dt, url)
    return found


def _discover_daily(cfg, start: datetime, end: datetime, cache_dir: Path) -> dict[datetime, str]:
    """Discover/stage one FINN point file per valid date from prioritized sources."""
    found: dict[datetime, str] = {}
    for src in _source_entries(cfg):
        cand = _discover_one_source(src, start, end, cache_dir)
        # earlier source candidates have priority
        for dt, path in cand.items():
            found.setdefault(dt, path)
    return found


def _dates(start, end):
    out = []
    d = datetime(start.year, start.month, start.day)
    e = datetime(end.year, end.month, end.day)
    while d <= e:
        out.append(d)
        d += timedelta(days=1)
    return out


def _integral(field: np.ndarray, mesh: MpasMesh) -> float:
    # Output fluxes use a common per-area unit for each field; area weighting is
    # sufficient for primary/fallback ratio diagnostics.
    return float(np.sum(np.asarray(field, np.float64) * mesh.area_cell))


def _format_output(template: str, *, year: int, mesh: MpasMesh, grid_name: str, start: datetime, end: datetime, freq: str) -> str:
    template = str(template)
    # Accept both Python {year} tokens used by inventory YAML and MPAS-Workflow
    # {{year}}/{{nCells}} tokens exported from Build.py.
    for key in ("year", "nCells", "grid", "mpas_grid_name", "freq", "start", "end", "start_mon", "start_day", "end_mon", "end_day"):
        template = template.replace("{{" + key + "}}", "{" + key + "}")
    return template.format(
        year=year,
        nCells=mesh.n_cells,
        mpas_grid_name=grid_name,
        grid=grid_name,
        freq=freq,
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
        start_mon=start.strftime("%b").lower(),
        start_day=start.strftime("%d"),
        end_mon=end.strftime("%b").lower(),
        end_day=end.strftime("%d"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--interior-only", action="store_true")
    ap.add_argument("--reject-outside", action="store_true")
    ap.add_argument("--max-distance-factor", type=float, default=2.5)
    ap.add_argument("--reuse-existing", action="store_true")
    ap.add_argument("--year", type=int, default=None, help="override config year while preserving month/day range")
    ap.add_argument("--grid-name", default="", help="MPAS grid label, e.g. x1.163842 or x6.828394")
    ap.add_argument("--output-file", default=None, help="override hourly output filename template")
    ap.add_argument("--prm-stats-output-file", default=None, help="override PRM daily fire-statistics filename template")
    ap.add_argument("--prm-area-output-file", default=None, help="deprecated alias for --prm-stats-output-file")
    frp_group = ap.add_mutually_exclusive_group()
    frp_group.add_argument("--prm-use-frp", dest="prm_use_frp", action="store_true",
                           help="model requests FRP-derived heat flux; missing optional FRP fields warn and are zero-filled")
    frp_group.add_argument("--prm-no-frp", dest="prm_use_frp", action="store_false",
                           help="model uses prescribed heat flux; missing optional FRP fields are zero-filled")
    ap.set_defaults(prm_use_frp=None)
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    lock_path = Path(a.output_dir) / ".finn_prepare.lock"
    with file_lock(lock_path):
        return _run_locked(a, cfg)


def _run_locked(a, cfg):
    start = datetime.strptime(str(cfg["start_date"]), "%Y-%m-%d")
    end = datetime.strptime(str(cfg["end_date"]), "%Y-%m-%d")
    if a.year is not None:
        if start.year != end.year:
            raise ValueError("--year override requires start_date/end_date in the same template year")
        start = start.replace(year=a.year)
        end = end.replace(year=a.year)
    mesh = MpasMesh.open(a.mesh)
    species_map = dict(cfg.get("species_map", {}))
    species_type = dict(cfg.get("species_type", {}))
    if not species_map:
        raise ValueError("FINN species_map is empty")
    hourly = bool(cfg.get("HOURLY", True))
    if not hourly:
        raise ValueError("current MPAS forecast streams use an hourly FINN file; set HOURLY: true")
    profile = np.asarray(cfg.get("lt_fac", [1] * 24), float)

    source_cache = Path(a.output_dir) / ".finn_source_cache"
    primary = _discover_daily(cfg, start, end, source_cache / "primary")
    if not primary:
        raise FileNotFoundError("No primary FINN daily files found in configured date range")

    # Plume-rise fire properties are deliberately discovered independently of
    # the emissions mechanism file.  This lets a FINNv1 emissions fallback be
    # used for a bad/missing MOZART interval without silently replacing the PRM
    # fire-size/FRP statistics.
    prm_cfg = dict(cfg.get("prm", {}))
    prm_use_frp = bool(prm_cfg.get("use frp", False)) if a.prm_use_frp is None else bool(a.prm_use_frp)
    # A dedicated base_FRP source is optional.  The current PRM author states
    # that only prm_lowbc_area_avg is mandatory; FINN MOZART each-fire files
    # already contain AREA, so they can supply the required fire-size mean when
    # base_FRP is absent.  Dedicated PRM sources are still preferred because
    # they may also provide FRP.
    prm_sources = {}
    if prm_cfg and bool(prm_cfg.get("enabled", True)):
        prm_sources = _discover_daily(prm_cfg, start, end, source_cache / "prm")
    prm_max_linear_missing = int(prm_cfg.get("max linear missing days", 1))

    gap_cfg = dict(cfg.get("missing_days", {}))
    max_linear_missing = int(gap_cfg.get("max linear missing days", 0))
    fallback_cfg = gap_cfg.get("fallback") or None
    fallback = _discover_daily(fallback_cfg, start, end, source_cache / "fallback") if fallback_cfg else {}
    fallback_species_map = dict((fallback_cfg or {}).get("species_map", species_map))
    fallback_species_type = dict((fallback_cfg or {}).get("species_type", species_type))
    fallback_scale_method = str((fallback_cfg or {}).get("scale method", "none")).lower()
    if fallback_scale_method not in {"none", "fixed", "robust_median_ratio"}:
        raise ValueError("fallback scale method must be none, fixed, or robust_median_ratio")

    forced_fallback_ranges = []
    for pair in (fallback_cfg or {}).get("force date ranges", []) or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("fallback force date ranges entries must be [start_date, end_date]")
        fs = datetime.strptime(str(pair[0]), "%Y-%m-%d").replace(year=start.year if a.year is not None else datetime.strptime(str(pair[0]), "%Y-%m-%d").year)
        fe = datetime.strptime(str(pair[1]), "%Y-%m-%d").replace(year=end.year if a.year is not None else datetime.strptime(str(pair[1]), "%Y-%m-%d").year)
        forced_fallback_ranges.append((fs, fe))

    def force_fallback(dt):
        return any(lo <= dt <= hi for lo, hi in forced_fallback_ranges)

    outdir = Path(a.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    year = start.year
    grid_name = a.grid_name or f"x1.{mesh.n_cells}"
    canonical = outdir / f"FINNv2.5.1_modvrs_nrt_MOZART_{year}_{grid_name}.static_hourly_netcdf3.nc"
    output_template = a.output_file or cfg.get("output_file", cfg.get("output_file_pattern", canonical.name))
    main_out = outdir / _format_output(output_template, year=year, mesh=mesh, grid_name=grid_name, start=start, end=end, freq="hourly")
    prm_template = (
        a.prm_stats_output_file
        or a.prm_area_output_file
        or cfg.get("prm_stats_output_file")
        or cfg.get("prm_area_output_file")
        or "FINNv2.5.1_modvrs_nrt_PRM_{year}_{grid}.static_daily_{start_mon}{start_day}-{end_mon}{end_day}.nc"
    )
    prm_out = outdir / _format_output(prm_template, year=year, mesh=mesh, grid_name=grid_name, start=start, end=end, freq="daily")

    for p in (main_out, prm_out, canonical):
        if p.is_symlink():
            p.unlink()
    if a.reuse_existing and main_out.exists() and prm_out.exists():
        # An AREA-average-only PRM file is valid under the current author
        # contract; optional lowbc fields are warning-only.
        try:
            from netCDF4 import Dataset
            with Dataset(prm_out) as ds:
                have = set(ds.variables)
            # PRM-author guidance: only fire-size average is mandatory.
            # Optional area-std/FRP variables may be absent in a valid existing
            # file; validate_streams will report those as warnings.
            required = {"firesize_biob_modis_avg"}
            reuse_prm = required.issubset(have)
        except Exception:
            reuse_prm = False
        if reuse_prm:
            if canonical != main_out and not canonical.exists():
                canonical.symlink_to(main_out.name)
            print(f"FINN outputs reused: {main_out}, {prm_out}")
            return

    primary_cache: OrderedDict[datetime, dict] = OrderedDict()
    fallback_cache: OrderedDict[datetime, dict] = OrderedDict()
    prm_cache: OrderedDict[datetime, dict] = OrderedDict()
    prm_primary_area_cache: OrderedDict[datetime, dict] = OrderedDict()
    prm_fallback_cache: OrderedDict[datetime, dict] = OrderedDict()
    accepted = total = 0
    prm_accepted = prm_total = 0
    prm_warning_messages: list[str] = []

    def _warn_prm(message: str):
        # Deduplicate warnings so a month-long run does not print the same
        # optional-field notice once per day.
        if message not in prm_warning_messages:
            prm_warning_messages.append(message)
            print(f"WARNING FINN PRM: {message}", file=sys.stderr)

    def load_day(dt, *, source="primary"):
        nonlocal accepted, total
        is_primary = source == "primary"
        cache = primary_cache if is_primary else fallback_cache
        paths = primary if is_primary else fallback
        if dt in cache:
            value = cache.pop(dt)
            cache[dt] = value
            return value
        if dt not in paths:
            raise KeyError(dt)
        scfg = cfg if is_primary else fallback_cfg
        smap = species_map if is_primary else fallback_species_map
        stype = species_type if is_primary else fallback_species_type
        local_path = _localize_source(paths[dt], source_cache / ("primary_downloads" if is_primary else "fallback_downloads"), dt)
        day, na, nt = _aggregate(
            _read_finn_csv(local_path), mesh, smap, stype,
            a.interior_only, a.reject_outside, a.max_distance_factor,
            scaling_cfg=scfg,
        )
        accepted += na
        total += nt
        cache[dt] = day
        while len(cache) > 8:
            cache.popitem(last=False)
        return day

    def _prm_stats_from_path(local_path, *, area_columns, frp_columns, source_label):
        """Read one FINN text source and return PRM stats plus FRP availability."""
        df = _read_finn_csv(local_path)
        has_frp = _find_column(df.columns, frp_columns) is not None
        stats, na, nt = _aggregate_prm_stats(
            df, mesh,
            interior_only=a.interior_only, reject_outside=a.reject_outside,
            max_distance_factor=a.max_distance_factor,
            area_columns=tuple(area_columns),
            frp_columns=tuple(frp_columns),
            require_frp=False,
        )
        if not has_frp:
            mode_note = (
                "config_do_FRP=true; model execution can continue, but the optional "
                "FRP avg/std fields are zero-filled and should not be interpreted as "
                "observed FRP."
                if prm_use_frp else
                "optional FRP avg/std are absent and are zero-filled (prescribed-heat mode)."
            )
            _warn_prm(f"{source_label}: {mode_note}")
        return stats, na, nt, has_frp

    def load_prm_day(dt):
        """Load a dedicated PRM/base_FRP source when one is available."""
        nonlocal prm_accepted, prm_total
        if dt in prm_cache:
            value = prm_cache.pop(dt)
            prm_cache[dt] = value
            return value
        if dt not in prm_sources:
            raise KeyError(dt)
        local_path = _localize_source(prm_sources[dt], source_cache / "prm_downloads", dt)
        stats, na, nt, _ = _prm_stats_from_path(
            local_path,
            area_columns=prm_cfg.get("area columns", ["AREA", "FIRE_AREA", "area"]),
            frp_columns=prm_cfg.get("frp columns", ["FRP", "frp"]),
            source_label="dedicated FINN PRM source",
        )
        prm_accepted += na
        prm_total += nt
        prm_cache[dt] = stats
        while len(prm_cache) > 8:
            prm_cache.popitem(last=False)
        return stats

    def load_prm_primary_area_day(dt):
        """Use the primary FINN MOZART file for the required AREA average.

        The uploaded 2024 MOZART sample contains the same AREA values/records as
        its matching base_FRP file.  Thus base_FRP is not a hard dependency for
        the current PRM contract; it is only needed when the optional FRP fields
        are desired.
        """
        nonlocal prm_accepted, prm_total
        if dt in prm_primary_area_cache:
            value = prm_primary_area_cache.pop(dt)
            prm_primary_area_cache[dt] = value
            return value
        if dt not in primary:
            raise KeyError(dt)
        local_path = _localize_source(primary[dt], source_cache / "primary_prm_area", dt)
        stats, na, nt, _ = _prm_stats_from_path(
            local_path,
            area_columns=prm_cfg.get("area columns", ["AREA", "FIRE_AREA", "area"]),
            frp_columns=prm_cfg.get("frp columns", ["FRP", "frp"]),
            source_label="FINN MOZART AREA fallback for PRM",
        )
        prm_accepted += na
        prm_total += nt
        prm_primary_area_cache[dt] = stats
        while len(prm_primary_area_cache) > 8:
            prm_primary_area_cache.popitem(last=False)
        return stats

    def load_prm_fallback_day(dt):
        """Use FINNv1 (or another configured fallback) for required PRM AREA.

        Only fire-size average is mandatory according to the current PRM author.
        AREA std is computed from the same records when possible.  FRP avg/std
        are retained when the source provides FRP; otherwise they are optional
        zero-filled fields and generate a warning, never a preprocessing error.
        """
        nonlocal prm_accepted, prm_total
        if dt in prm_fallback_cache:
            value = prm_fallback_cache.pop(dt)
            prm_fallback_cache[dt] = value
            return value
        if dt not in fallback:
            raise KeyError(dt)
        local_path = _localize_source(fallback[dt], source_cache / "fallback_prm_downloads", dt)
        stats, na, nt, _ = _prm_stats_from_path(
            local_path,
            area_columns=prm_cfg.get(
                "fallback area columns",
                prm_cfg.get("area columns", ["AREA", "FIRE_AREA", "area"]),
            ),
            frp_columns=prm_cfg.get(
                "fallback frp columns",
                prm_cfg.get("frp columns", ["FRP", "frp"]),
            ),
            source_label="FINNv1/fallback PRM source",
        )
        prm_accepted += na
        prm_total += nt
        prm_fallback_cache[dt] = stats
        while len(prm_fallback_cache) > 8:
            prm_fallback_cache.popitem(last=False)
        return stats

    def _load_best_prm_source(dt):
        """Return the best available actual source for one date."""
        if dt in prm_sources:
            return load_prm_day(dt), "dedicated-prm"
        if dt in primary:
            return load_prm_primary_area_day(dt), "primary-mozart-area"
        if dt in fallback:
            return load_prm_fallback_day(dt), "fallback-area"
        raise KeyError(dt)

    def resolve_prm_day(dt):
        """Resolve one daily PRM record with only fire-size mean mandatory."""
        # A user-forced FINNv1 interval is honored for PRM fire size as well.
        # Optional FRP may be zero-filled; this is warning-only by design.
        if force_fallback(dt) and dt in fallback:
            return load_prm_fallback_day(dt), "fallback-area-forced"

        # Prefer the dedicated base_FRP product; otherwise the matching MOZART
        # file provides the required AREA field.
        try:
            return _load_best_prm_source(dt)
        except (KeyError, FileNotFoundError):
            pass

        # Short isolated gaps may be interpolated from the best available
        # neighboring PRM/AREA sources.  Long gaps fall through to the explicit
        # FINNv1 fallback if available.
        source_dates = sorted(set(prm_sources) | set(primary))
        max_search = max(1, prm_max_linear_missing + 1)
        before = after = None
        before_day = after_day = None
        for n in range(1, max_search + 1):
            cand = dt - timedelta(days=n)
            if cand in source_dates:
                try:
                    before, _ = _load_best_prm_source(cand)
                    before_day = cand
                    break
                except (KeyError, FileNotFoundError):
                    pass
        for n in range(1, max_search + 1):
            cand = dt + timedelta(days=n)
            if cand in source_dates:
                try:
                    after, _ = _load_best_prm_source(cand)
                    after_day = cand
                    break
                except (KeyError, FileNotFoundError):
                    pass
        if before is not None and after is not None:
            span = (after_day - before_day).days - 1
            if span <= prm_max_linear_missing:
                w = (dt - before_day).total_seconds() / (after_day - before_day).total_seconds()
                fields = set(before) & set(after)
                out = {k: (1.0-w)*before[k] + w*after[k] for k in fields}
                return out, f"linear-prm:{before_day:%Y-%m-%d}->{after_day:%Y-%m-%d}"

        if dt in fallback:
            return load_prm_fallback_day(dt), "fallback-area"

        raise ValueError(
            f"FINN PRM fire-size average missing {dt:%Y-%m-%d}; no actual AREA source, "
            f"no bracket within max linear missing days={prm_max_linear_missing}, "
            "and no configured FINNv1/fallback AREA file. prm_lowbc_area_avg is the "
            "only current hard PRM requirement."
        )

    # Optional robust fallback normalization.  The default is deliberately NONE:
    # project diagnostics found that overlap-derived mean factors were dominated
    # by episodic fire spikes and produced unrealistic PM2.5/AOD.
    fallback_factor = {}
    if fallback_cfg:
        if fallback_scale_method == "fixed":
            fixed = dict(fallback_cfg.get("fixed factors", {}))
            for dst in species_map.values():
                fallback_factor[dst] = float(fixed.get(dst, fixed.get("default", 1.0)))
        elif fallback_scale_method == "robust_median_ratio":
            overlap = sorted(set(primary) & set(fallback))
            limit = int(fallback_cfg.get("max overlap days", 60))
            if limit > 0 and len(overlap) > limit:
                ii = np.linspace(0, len(overlap) - 1, limit).round().astype(int)
                overlap = [overlap[i] for i in np.unique(ii)]
            ratios: dict[str, list[float]] = {}
            for dt in overlap:
                pday = load_day(dt, source="primary")
                fday = load_day(dt, source="fallback")
                for field in set(pday) & set(fday):
                    if field == "firesize_biob_modis_avg":
                        continue
                    den = _integral(fday[field], mesh)
                    num = _integral(pday[field], mesh)
                    if np.isfinite(num) and np.isfinite(den) and den > 0 and num >= 0:
                        ratios.setdefault(field, []).append(num / den)
            clip = fallback_cfg.get("ratio clip", [0.25, 4.0])
            lo, hi = float(clip[0]), float(clip[1])
            min_samples = int(fallback_cfg.get("minimum overlap days", 3))
            for field in species_map.values():
                rr = np.asarray(ratios.get(field, []), float)
                if rr.size < min_samples:
                    fallback_factor[field] = 1.0
                else:
                    fallback_factor[field] = float(np.clip(np.nanmedian(rr), lo, hi))
        else:
            fallback_factor = {dst: 1.0 for dst in species_map.values()}

    def apply_fallback_factor(day):
        out = {}
        for field, arr in day.items():
            if field == "firesize_biob_modis_avg":
                out[field] = arr
            else:
                out[field] = arr * float(fallback_factor.get(field, 1.0))
        return out

    pdates = sorted(primary)
    def primary_bracket(dt):
        before = [x for x in pdates if x < dt]
        after = [x for x in pdates if x > dt]
        return (before[-1] if before else None, after[0] if after else None)

    resolved_status = []
    dates = _dates(start, end)
    main_fields = [dst for src, dst in species_map.items() if species_type.get(src, "gas").lower() != "scalar" and dst != "co2_biob_modis"]
    if not main_fields:
        raise ValueError("FINN has no non-scalar emissions fields for the forecast stream")
    prm_fields = ["firesize_biob_modis_avg", "firesize_biob_modis_std", "frp_biob_modis_avg", "frp_biob_modis_std"]

    attrs = {
        "inventory": "FINN",
        "mesh_fingerprint": mesh.fingerprint,
        "attribution": "Original ESMF emissions-regridding methodology and utility lineage: Duseong Jo (2021)",
        "point_assignment": "spherical cKDTree nearest MPAS cell center",
        "scaling": json.dumps(describe_scaling(cfg), sort_keys=True),
        "missing_day_policy": "short linear interpolation, long-gap fallback",
        "fallback_scale_method": fallback_scale_method,
    }

    prm_attrs = dict(attrs)
    prm_attrs.update({
        "inventory_component": "FINN each-fire fire properties for MPAS-GOCART2G PRM",
        "aggregation": "per-MPAS-cell population mean/std over accepted FINN each-fire records",
        "config_do_FRP": str(prm_use_frp).lower(),
        "required_prm_field": "firesize_biob_modis_avg",
        "optional_prm_fields": "firesize_biob_modis_std,frp_biob_modis_avg,frp_biob_modis_std",
        "long_gap_prm_fallback": "FINNv1/fallback AREA supplies required fire-size mean; optional FRP may be zero-filled",
    })
    prm_field_attrs = {
        "firesize_biob_modis_avg": {"units": "m2", "long_name": "Active Fire Size Average"},
        "firesize_biob_modis_std": {"units": "m2", "long_name": "Active Fire Size Standard Deviation"},
        "frp_biob_modis_avg": {"units": "MW", "long_name": "Fire Radiative Power Average"},
        "frp_biob_modis_std": {"units": "MW", "long_name": "Fire Radiative Power Standard Deviation"},
    }

    with MpasEmissionStreamWriter(main_out, n_cells=mesh.n_cells, field_names=main_fields, attrs=attrs) as main_writer, \
         MpasEmissionStreamWriter(prm_out, n_cells=mesh.n_cells, field_names=prm_fields, attrs=prm_attrs, field_attrs=prm_field_attrs) as prm_writer:
        for dt in dates:
            if force_fallback(dt):
                if dt not in fallback:
                    raise ValueError(f"forced FINN fallback date {dt:%Y-%m-%d} has no fallback file")
                day = apply_fallback_factor(load_day(dt, source="fallback"))
                status = "fallback-forced"
            elif dt in primary:
                day = load_day(dt, source="primary")
                status = "primary"
            else:
                b, e = primary_bracket(dt)
                missing_span = None if b is None or e is None else (e - b).days - 1
                if b is not None and e is not None and missing_span <= max_linear_missing:
                    aafter = (dt - b).total_seconds() / (e - b).total_seconds()
                    bd = load_day(b, source="primary")
                    ed = load_day(e, source="primary")
                    day = {k: (1.0-aafter)*bd[k] + aafter*ed[k] for k in set(bd) & set(ed)}
                    status = f"linear:{b:%Y-%m-%d}->{e:%Y-%m-%d}"
                elif dt in fallback:
                    day = apply_fallback_factor(load_day(dt, source="fallback"))
                    status = "fallback"
                else:
                    raise ValueError(
                        f"FINN missing {dt:%Y-%m-%d}; no acceptable short interpolation bracket and no fallback file. "
                        f"Primary bracket={b}..{e}, missing span={missing_span}, max linear={max_linear_missing}."
                    )
            missing = [f for f in main_fields if f not in day]
            if missing:
                raise KeyError(f"{dt:%Y-%m-%d}: resolved FINN day lacks fields {missing}")
            hourly_by_field = {
                field: apply_diurnal_profile(day[field], mesh.local_hour_offset, profile)
                for field in main_fields
            }
            for hour in range(24):
                vals = {field: hourly_by_field[field][hour] for field in main_fields}
                main_writer.append(dt.replace(hour=hour), vals)
            prm_day, prm_status = resolve_prm_day(dt)
            if "firesize_biob_modis_avg" not in prm_day:
                raise KeyError(
                    f"{dt:%Y-%m-%d}: resolved FINN PRM day lacks required field "
                    "firesize_biob_modis_avg"
                )
            # Keep workflow-generated files maximally compatible with the
            # current four-stream template.  Optional fields absent from an
            # external/source record are zero-filled and warning-only.
            for field in prm_fields:
                if field not in prm_day:
                    _warn_prm(f"{dt:%Y-%m-%d}: optional {field} absent; zero-filled")
                    prm_day[field] = np.zeros(mesh.n_cells, dtype=np.float64)
            prm_writer.append(dt, {f: prm_day[f] for f in prm_fields})
            resolved_status.append({"date": dt.strftime("%Y-%m-%d"), "source": status, "prm_source": prm_status})

    if canonical != main_out:
        if canonical.exists() or canonical.is_symlink(): canonical.unlink()
        canonical.symlink_to(main_out.name)

    provenance = {
        "inventory": "FINN",
        "date_range": [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")],
        "primary_days": len(primary),
        "fallback_days_available": len(fallback),
        "day_resolution": resolved_status,
        "fallback_scale_method": fallback_scale_method,
        "fallback_factors": fallback_factor,
        "forced_fallback_ranges": [[a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")] for a,b in forced_fallback_ranges],
        "emissions_scaling": describe_scaling(cfg),
        "mesh_fingerprint": mesh.fingerprint,
        "accepted_point_records_loaded": accepted,
        "total_point_records_loaded": total,
        "main_output": str(main_out),
        "prm_stats_output": str(prm_out),
        "prm_source_days": len(prm_sources),
        "prm_accepted_point_records_loaded": prm_accepted,
        "prm_total_point_records_loaded": prm_total,
        "prm_use_frp": prm_use_frp,
        "prm_required_field": "firesize_biob_modis_avg",
        "prm_optional_fields": [
            "firesize_biob_modis_std",
            "frp_biob_modis_avg",
            "frp_biob_modis_std",
        ],
        "prm_warnings": prm_warning_messages,
        "prm_fallback_area_days_used": sum(1 for x in resolved_status if str(x.get("prm_source", "")).startswith("fallback-area")),
    }
    main_out.with_suffix(main_out.suffix + ".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"FINN hourly output: {main_out}")
    print(f"FINN PRM fire-statistics output: {prm_out}")
    print(f"accepted emissions point records loaded: {accepted}/{total}")
    print(f"accepted PRM point records loaded: {prm_accepted}/{prm_total}")


if __name__ == "__main__":
    main()
