#!/usr/bin/env python3
"""Prepare MERRA-2/GMI chemistry intermediates for MPAS init_atmosphere.

The wrapper intentionally starts from the raw aerosol and OVP resources rather
than a prebuilt MPAS chemistry file.  It drives the project's
``init_MPAS-GOCART2G/run_processing.py`` and makes missing required chemistry
fields fatal by default.  HNO3/NH3 scaling is supported as an explicit
sensitivity on the raw chemistry source (separate from emission scaling).
"""
from __future__ import annotations
import argparse, calendar, importlib.util, json, os, sys, tempfile
from datetime import datetime
from pathlib import Path
import yaml

from .resource import obtain, link_or_copy


def _source_date(valid: datetime, src_cfg: dict) -> datetime:
    year = src_cfg.get("source year", "same")
    year = valid.year if str(year).lower() == "same" else int(year)
    day = valid.day
    try:
        return valid.replace(year=year)
    except ValueError:
        if valid.month == 2 and valid.day == 29 and str(src_cfg.get("leap day fallback", "feb28")).lower() == "feb28":
            return valid.replace(year=year, day=28)
        raise


def _vars(path: Path):
    from netCDF4 import Dataset
    with Dataset(path) as ds:
        return set(ds.variables)


def _required_for_file(species_map, file_key):
    return {str(x["source"]) for x in species_map if str(x.get("file", "prefix_in_1")) == file_key}


def _load_processor(processor_dir: Path | None):
    """Load an optional external processor, else use the bundled issue-#2 converter."""
    if processor_dir is None:
        from . import merra_intermediate as mod
        return mod
    path = processor_dir / "run_processing.py"
    if not path.exists():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location("mpas_gocart_merra_processor", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _template_vars(valid: datetime, sd: datetime):
    return {
        "valid_year": valid.year,
        "valid_month": f"{valid.month:02d}",
        "valid_day": f"{valid.day:02d}",
        "valid_hour": valid.hour,
        "valid_hh": f"{valid.hour:02d}",
        "valid_ymd": valid.strftime("%Y%m%d"),
        "source_year": sd.year,
        "source_month": f"{sd.month:02d}",
        "source_day": f"{sd.day:02d}",
        "source_hour": sd.hour,
        "source_hh": f"{sd.hour:02d}",
        "source_ymd": sd.strftime("%Y%m%d"),
    }


def _apply_scaling(proc_cfg: dict, cfg: dict):
    scaling = {str(k).upper(): float(v) for k, v in dict(cfg.get("scaling", {})).items()}
    provenance = {}
    for item in proc_cfg["species_map"]:
        aliases = {str(item.get("source", "")).upper(), str(item.get("target", "")).upper()}
        fac = 1.0
        for a in aliases:
            if a in scaling:
                fac *= scaling[a]
        if fac != 1.0:
            item["weight"] = float(item.get("weight", 1.0)) * fac
        provenance[str(item.get("target"))] = fac
    return provenance


def _resolve_input(cfg: dict, key: str, file_key: str, species, valid: datetime,
                   cache: Path):
    scfg = dict(cfg[key])
    sd = _source_date(valid, scfg)
    tv = _template_vars(valid, sd)
    candidates = scfg.get("local files", scfg.get("local file", []))
    candidates = candidates if isinstance(candidates, list) else [candidates]
    urls = scfg.get("urls", scfg.get("url", []))
    urls = urls if isinstance(urls, list) else [urls]
    source_name = str(scfg["source filename"]).format(**tv)
    src, meta = obtain(
        local_candidates=candidates,
        url_candidates=urls,
        cache_dir=cache / key,
        output_name=source_name,
        template_vars=tv,
    )
    req = _required_for_file(species, file_key)
    missing = sorted(req - _vars(src))
    if missing and str(cfg.get("missing field policy", "error")).lower() == "error":
        raise KeyError(f"{src}: missing required chemistry fields: {missing}")
    return src, scfg, sd, {
        **meta,
        "source_date": sd.strftime("%Y-%m-%d_%H"),
        "source_filename": source_name,
        "temporal_representation": str(scfg.get("temporal representation", "unspecified")),
        "required_fields": sorted(req),
        "missing_fields": missing,
    }


def _run_processor_once(base_proc_cfg: dict, cfg: dict, processor_dir: Path,
                        resolved: dict, valid: datetime, out: Path, work: Path):
    """Run the legacy converter for exactly one target valid time."""
    import copy
    proc_cfg = copy.deepcopy(base_proc_cfg)
    p = proc_cfg["control_params"]
    work.mkdir(parents=True, exist_ok=True)

    for file_key, (src, scfg, _) in resolved.items():
        idx = file_key.split("_")[-1]
        suffix_key = f"suffix_in_{idx}"
        suffix = str(scfg.get("processor suffix", p.get(suffix_key, ".nc4")))
        alias_prefix = work / f"source_{idx}."
        # run_processing.py forms prefix + YYYYMMDD + suffix.
        alias = Path(str(alias_prefix) + valid.strftime("%Y%m%d") + suffix)
        link_or_copy(src, alias)
        p[file_key] = str(alias_prefix)
        p[suffix_key] = suffix

    # interval_hr=24 with num_days=1 forces exactly one iteration, while
    # preserving the requested target hour in the intermediate filename.
    p.update(
        start_year=valid.year, start_month=valid.month, start_day=valid.day,
        start_hour=valid.hour, num_days=1, interval_hr=24,
        prefix_out=str(out / "MERRA2:"),
    )
    scaling = _apply_scaling(proc_cfg, cfg)
    tmpcfg = work / "processor_config.yaml"
    tmpcfg.write_text(yaml.safe_dump(proc_cfg, sort_keys=False))
    mod = _load_processor(processor_dir)
    mod.run_conversion(str(tmpcfg))
    expected = out / f"MERRA2:{valid:%Y-%m-%d_%H}"
    if not expected.exists():
        raise RuntimeError(f"MERRA chemistry processor did not create: {expected}")
    return expected, scaling


def prepare(config_file: str, valid: datetime, output_dir: str,
            processor_dir_override: str | None = None):
    config_path = Path(config_file).expanduser().resolve()
    cfg = yaml.safe_load(config_path.read_text())
    processor_raw = processor_dir_override or cfg.get("processor directory", "")
    processor_raw = os.path.expandvars(str(processor_raw)).strip()
    processor_dir = Path(processor_raw).expanduser() if processor_raw else None
    base_raw = os.path.expandvars(str(cfg.get("processor config", "merra_processor.yaml")))
    base_cfg = Path(base_raw).expanduser()
    if not base_cfg.is_absolute():
        base_cfg = config_path.parent / base_cfg
    base_proc_cfg = yaml.safe_load(base_cfg.read_text())
    species = base_proc_cfg["species_map"]

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache = Path(os.path.expandvars(str(
        cfg.get("raw cache directory", out / "raw")))).expanduser()
    cache.mkdir(parents=True, exist_ok=True)

    interval = int(cfg.get("interval hours", 6))
    mode = str(cfg.get("processing mode", "daily-container")).lower()
    provenance = {
        "valid_time": valid.strftime("%Y-%m-%d_%H"),
        "processing_mode": mode,
        "inputs": {},
        "scaling": {},
    }

    if mode in ("time-sliced", "timesliced", "single-time-files"):
        # One PrepareChemIC task corresponds to one requested MPAS valid time.
        # Do not regenerate the other 6-hour times for the same day.
        target = valid.replace(minute=0, second=0, microsecond=0)
        resolved = {}
        target_meta = {}
        for key, file_key in (("aerosol", "prefix_in_1"), ("ovp", "prefix_in_2")):
            src, scfg, sd, meta = _resolve_input(
                cfg, key, file_key, species, target, cache)
            resolved[file_key] = (src, scfg, sd)
            target_meta[key] = meta
        expected, scaling = _run_processor_once(
            base_proc_cfg, cfg, processor_dir, resolved, target, out,
            out / f"work_{target:%Y%m%d_%H}")
        outputs = [expected]
        provenance["inputs"][f"{target:%H}"] = target_meta
        provenance["scaling"] = scaling

    else:
        # Legacy/daily-container mode: one raw file may hold all 6-hour records.
        import copy
        proc_cfg = copy.deepcopy(base_proc_cfg)
        resolved = {}
        for key, file_key in (("aerosol", "prefix_in_1"), ("ovp", "prefix_in_2")):
            src, scfg, sd, meta = _resolve_input(
                cfg, key, file_key, species, valid, cache)
            resolved[file_key] = (src, scfg, sd)
            provenance["inputs"][key] = meta

        work = out / f"work_{valid:%Y%m%d}"
        work.mkdir(exist_ok=True)
        pctl = proc_cfg["control_params"]
        for file_key, (src, scfg, _) in resolved.items():
            idx = file_key.split("_")[-1]
            suffix_key = f"suffix_in_{idx}"
            suffix = str(scfg.get("processor suffix", pctl.get(suffix_key, ".nc4")))
            alias_prefix = work / f"source_{idx}."
            alias = Path(str(alias_prefix) + valid.strftime("%Y%m%d") + suffix)
            link_or_copy(src, alias)
            pctl[file_key] = str(alias_prefix)
            pctl[suffix_key] = suffix

        pctl.update(
            start_year=valid.year, start_month=valid.month, start_day=valid.day,
            start_hour=0, num_days=1, interval_hr=interval,
            prefix_out=str(out / "MERRA2:"),
        )
        provenance["scaling"] = _apply_scaling(proc_cfg, cfg)
        tmpcfg = work / "processor_config.yaml"
        tmpcfg.write_text(yaml.safe_dump(proc_cfg, sort_keys=False))
        mod = _load_processor(processor_dir)
        mod.run_conversion(str(tmpcfg))
        outputs = [
            out / f"MERRA2:{valid:%Y-%m-%d}_{h:02d}"
            for h in range(0, 24, interval)
        ]
        missing = [str(x) for x in outputs if not x.exists()]
        if missing:
            raise RuntimeError(
                f"MERRA chemistry processor did not create: {missing}")

    provenance["outputs"] = [str(x) for x in outputs]
    (out / f"MERRA2_{valid:%Y%m%d}.provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True))
    return outputs

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("config"); ap.add_argument("--valid", required=True); ap.add_argument("--output-dir", required=True); ap.add_argument("--processor-dir", default=None)
    a=ap.parse_args(); valid=datetime.strptime(a.valid, "%Y%m%d%H")
    prepare(a.config, valid, a.output_dir, a.processor_dir)

if __name__ == "__main__": main()
