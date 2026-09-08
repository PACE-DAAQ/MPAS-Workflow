"""Validate emissions/PRM files referenced by a resolved MPAS streams file.

Only streams actually consumed by the forecast are checked: ``*_emissions``
and ``prm_lowbc_*``.  Classic NetCDF/CDF-1/2/5 are accepted; NetCDF4/HDF5 is
rejected for MPAS-v8+/SMIOL-facing inputs.

The upstream PRM source may lack FRP or spread information, but the current
gocartMPAS implementation unconditionally reads all four lowbc streams.
Therefore every MPAS-facing staged PRM file must contain AREA mean/std and
FRP mean/std.  Workflow preprocessing may zero-fill unavailable optional
source information before staging the file.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import sys
from .io import netcdf_container_format

STREAM_RE = re.compile(r'<stream\s+name="([^"]+)"(.*?)</stream>', re.S)
FILE_RE = re.compile(r'filename_template="([^"]+)"')
VAR_RE = re.compile(r'<var\s+name="([^"]+)"\s*/>')

def parse_emission_streams(text: str):
    out=[]
    for name, body in STREAM_RE.findall(text):
        if not (name.endswith('_emissions') or name.startswith('prm_lowbc_')):
            continue
        fm=FILE_RE.search(body)
        if not fm: raise ValueError(f'stream {name} has no filename_template')
        out.append((name, fm.group(1), VAR_RE.findall(body)))
    return out


def _all_zero(path: Path, var: str) -> bool:
    """True when *var* is present but identically zero everywhere.

    MPAS accepts a PRM file whose fields are all zero and then silently produces
    no plume rise, so 'the variable exists' is not enough to trust a run.
    """
    from netCDF4 import Dataset
    import numpy as np
    with Dataset(path) as ds:
        if var not in ds.variables:
            return True
        arr = np.asarray(ds.variables[var][:])
    return not bool(np.any(np.isfinite(arr) & (arr != 0.0)))


def _variables(path: Path) -> set[str]:
    try:
        from netCDF4 import Dataset
    except ImportError:
        import subprocess
        proc=subprocess.run(['ncdump','-h',str(path)],capture_output=True,text=True)
        if proc.returncode: raise RuntimeError(proc.stderr)
        return set(re.findall(r'\b(?:float|double|int|char|byte|short|uint|int64|uint64)\s+([A-Za-z0-9_]+)\s*\(',proc.stdout))
    with Dataset(path) as ds:
        return set(ds.variables)


def validate(streams_file, directory='.', only_prm=False, require_nonzero=()):
    streams=Path(streams_file)
    base=Path(directory)
    items=parse_emission_streams(streams.read_text())
    if only_prm:
        items=[i for i in items if i[0].startswith('prm_lowbc_')]
        if not items: raise ValueError(f'no prm_lowbc_* streams found in {streams}')
    elif not items: raise ValueError(f'no emissions streams found in {streams}')
    failures=[]
    seen={}

    for name, filename, required in items:
        if '{{' in filename or '$' in filename:
            failures.append(f'{name}: unresolved filename placeholder: {filename}')
            continue
        path=base/filename
        if not path.exists():
            failures.append(f'{name}: missing file {path}')
            continue
        fmt=netcdf_container_format(path)
        if fmt == 'hdf5' or fmt == 'unknown':
            failures.append(f'{name}: MPAS-facing file has unsupported container {fmt}: {path}')
        if path not in seen:
            try: seen[path]=_variables(path)
            except Exception as exc:
                failures.append(f'{name}: cannot inspect {path}: {exc}')
                continue
        missing=[v for v in required if v not in seen[path]]
        if missing:
            failures.append(f'{name}: {path.name} lacks variables {missing}')

    # Presence is not sufficient: a PRM file whose fields are all zero makes the
    # plume-rise model a no-op that is indistinguishable from a working run.
    for var in require_nonzero:
        providers = [(name, base/filename) for name, filename, required in items
                     if var in required and '{{' not in filename and '$' not in filename]
        if not providers:
            failures.append(f'{var}: no stream in {streams.name} provides this variable')
            continue
        for name, path in providers:
            if not path.exists():
                continue
            try:
                empty = _all_zero(path, var)
            except Exception as exc:
                failures.append(f'{name}: cannot check {path.name} for non-zero {var}: {exc}')
                continue
            if empty:
                failures.append(
                    f'{name}: {path.name} has {var} identically zero; the plume-rise '
                    'model would run with no fire input while logging a normal run')

    if failures:
        raise RuntimeError('\n'.join(failures))
    return items


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('streams')
    ap.add_argument('--directory',default='.')
    ap.add_argument('--list-only',action='store_true')
    ap.add_argument('--only-prm',action='store_true',
                    help='check only the prm_lowbc_* streams')
    ap.add_argument('--require-nonzero',default='',
                    help='comma-separated variables that must not be identically zero')
    a=ap.parse_args()
    items=parse_emission_streams(Path(a.streams).read_text())
    if a.only_prm:
        items=[i for i in items if i[0].startswith('prm_lowbc_')]
    if a.list_only:
        for name,fn,vars_ in items: print(f'{name}: {fn}: {",".join(vars_)}')
        return
    validate(a.streams,a.directory,only_prm=a.only_prm,
             require_nonzero=[v for v in a.require_nonzero.split(',') if v])
    print(f'validated {len(items)} forecast emissions/PRM streams')

if __name__=='__main__': main()
