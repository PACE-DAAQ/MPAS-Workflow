#!/usr/bin/env python3
"""Verify that an experiment's RESOLVED configuration matches a reference.

Why resolved rather than the scenario file: a scenario can look correct and
still resolve differently, because values also come from scenarios/defaults/*
and from suite-specific code paths. config/auto/*.csh is what the tasks actually
read, so it is the thing worth comparing. In practice it is stable: two
different suites built from different scenarios produced byte-identical
model.csh and build.csh here.

Usage
  # record a reference from an experiment you trust
  check_experiment_config.py --record <experimentDir> -o reference.cfg

  # check another experiment (or your own, before submitting) against it
  check_experiment_config.py --check <experimentDir> -r reference.cfg

<experimentDir> is the directory holding MPAS-Workflow/config/auto, e.g.
/glade/derecho/scratch/$USER/pandac/<SuiteName>

Keys that legitimately differ between users or runs (experiment name, work
directories, the account) are ignored by default; everything else must match.
Exit status is 0 when the configurations agree and 1 when they do not, so this
can gate a submission.
"""
import argparse, re, sys
from pathlib import Path

# Files that define the scientific configuration. Deliberately NOT
# experiment.csh / workflow.csh / naming.csh, which are per-run identity.
FILES = ["model.csh", "build.csh", "emissions.csh", "initic.csh", "observations.csh"]

# Per-user or per-run by nature; differing here is not drift.
IGNORE = re.compile(r"""
    ^(ExperimentDirectory|mainScriptDir|SuiteName|ExperimentName|expSuffix
     |.*Account|.*WorkDir|.*WorkDirectory|firstCyclePoint|finalCyclePoint
     |restartCyclePoint|FirstCycleDate|nextFirstCycleDate|CYLC.*)$
""", re.X)

SETENV = re.compile(r'^\s*setenv\s+(\S+)\s+(.*?)\s*$')


def read_config(expdir: Path) -> dict:
    auto = expdir / "MPAS-Workflow" / "config" / "auto"
    if not auto.is_dir():
        auto = expdir / "config" / "auto"
    if not auto.is_dir():
        sys.exit(f"FATAL: no config/auto under {expdir}")
    cfg = {}
    for name in FILES:
        f = auto / name
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            m = SETENV.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip().strip('"')
            if IGNORE.match(key):
                continue
            cfg[f"{name}:{key}"] = val
    return cfg


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", metavar="EXPDIR")
    g.add_argument("--check", metavar="EXPDIR")
    p.add_argument("-o", "--out", metavar="FILE")
    p.add_argument("-r", "--reference", metavar="FILE")
    a = p.parse_args()

    if a.record:
        cfg = read_config(Path(a.record))
        out = Path(a.out) if a.out else Path("reference.cfg")
        out.write_text("".join(f"{k} = {v}\n" for k, v in sorted(cfg.items())))
        print(f"recorded {len(cfg)} settings from {a.record} -> {out}")
        return 0

    if not a.reference:
        sys.exit("FATAL: --check requires -r/--reference")
    ref = {}
    for line in Path(a.reference).read_text().splitlines():
        if " = " in line:
            k, v = line.split(" = ", 1)
            ref[k.strip()] = v.strip()
    cfg = read_config(Path(a.check))

    missing = sorted(k for k in ref if k not in cfg)
    extra = sorted(k for k in cfg if k not in ref)
    differ = sorted(k for k in ref if k in cfg and ref[k] != cfg[k])

    print(f"reference: {len(ref)} settings   experiment: {len(cfg)} settings")
    for k in differ:
        print(f"  DIFFERS  {k}\n      reference: {ref[k]}\n      yours    : {cfg[k]}")
    for k in missing:
        print(f"  MISSING  {k} (reference: {ref[k]})")
    for k in extra:
        print(f"  EXTRA    {k} = {cfg[k]}")

    if not (differ or missing or extra):
        print("\nMATCH: this experiment's resolved configuration is identical to the reference.")
        return 0
    print(f"\nMISMATCH: {len(differ)} differ, {len(missing)} missing, {len(extra)} extra.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
