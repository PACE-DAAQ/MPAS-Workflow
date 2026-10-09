#!/usr/bin/env python
"""Check that a staged suite is self-contained and matches its scenario, before it is installed.

Why this exists
---------------
A suite is normally generated from a scenario yaml. When one is instead created by COPYING another
suite's tree and hand-editing the generated files, three faults slip through silently, and all three
happened on 2026-10-09 while staging syha_r4_qfed_polar_new:

  1. `origin` in flow.cylc still named the source suite. It is the directory every task cds into, so
     the new suite ran using the SOURCE suite's entire configuration and wrote into its work
     directories -- while the new suite's own config sat unused.
  2. flow.cylc carried the source suite's RESUMED initial cycle point rather than the scenario's
     first cycle point, so the run would have started mid-period.
  3. The generated config/auto files were edited but the scenario yaml was not, so the suite and the
     thing that regenerates it disagreed. A later Run.py would have silently restored the old
     observers.

check_experiment_config.py compares the science configuration of two experiments. This is the
complementary check: that ONE suite is internally consistent and references nothing outside itself.

Usage
  check_suite_independence.py <experimentDir> [--scenario <scenario.yaml>]

<experimentDir> holds MPAS-Workflow/, e.g. /glade/derecho/scratch/$USER/pandac/<SuiteName>.
The scenario is read from config/auto/scenario.csh when not given.

Exit status is 0 when every check passes and 1 otherwise, so it can gate `cylc install`.
"""
import argparse
import os
import re
import sys
from pathlib import Path

SETENV = re.compile(r'^\s*setenv\s+(\S+)\s+(.*?)\s*$')
SETVAR = re.compile(r'^\s*set\s+(\S+)\s*=\s*(.*?)\s*$')
SEARCHED = ('flow.cylc', 'config', 'bin', 'tools')
# Paths that legitimately name other experiments: shared inputs, and our own backups.
ALLOWED = re.compile(r'(ExternalAnalyses_shared|/obs_bc/|/input/obs/|\.bak|/log/)')

failures = []
notes = []


def fail(check, detail):
    failures.append((check, detail))


def note(text):
    notes.append(text)


def read_csh(path):
    """setenv and set assignments from one csh config file."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        for pattern in (SETENV, SETVAR):
            m = pattern.match(line)
            if m:
                values[m.group(1)] = m.group(2).strip('"').strip()
                break
    return values


def scenario_observers(scenario_path):
    """The assimilated observers and monitors, read without a yaml parser.

    Only the FIRST block of each is taken: a scenario also carries an `hofx:` section with its own
    observers list, and merging the two would report a mismatch against config/auto that is not real.
    """
    observers, monitors, section = [], [], None
    for raw in Path(scenario_path).read_text().splitlines():
        stripped = raw.strip()
        if stripped.startswith('observers:') and not observers:
            section = observers
            continue
        if stripped.startswith('monitors:') and not monitors:
            section = monitors
            continue
        if stripped.startswith('- ') and section is not None:
            section.append(stripped[2:].split('#')[0].strip())
            continue
        if stripped and not stripped.startswith('-') and not stripped.startswith('#'):
            section = None
    return observers, monitors


def scenario_value(scenario_path, key):
    for raw in Path(scenario_path).read_text().splitlines():
        stripped = raw.strip()
        if stripped.startswith(f'{key}:'):
            return stripped.split(':', 1)[1].split('#')[0].strip()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('experiment')
    parser.add_argument('--scenario', default=None)
    args = parser.parse_args()

    exp = Path(args.experiment).resolve()
    work = exp / 'MPAS-Workflow'
    if not work.is_dir():
        work = exp                      # an installed cylc-run copy has no MPAS-Workflow level
    auto = work / 'config' / 'auto'
    if not auto.is_dir():
        sys.exit(f'FATAL: no config/auto under {work}')
    suite = exp.name
    print(f'suite   {suite}')
    print(f'tree    {work}')

    # ---------------------------------------------------------------- 1. no other suite referenced
    # A sibling is a directory beside this one, so anchor on the experiment's own parent rather
    # than on any path that happens to contain the experiment root's name.
    parent = re.escape(str(exp.parent))
    sibling = re.compile(parent + r'/(?!' + re.escape(suite) + r'(?:/|$))([A-Za-z0-9_.-]+)/')
    offenders = {}
    for entry in SEARCHED:
        target = work / entry
        paths = [target] if target.is_file() else sorted(target.rglob('*')) if target.is_dir() else []
        for path in paths:
            if not path.is_file() or ALLOWED.search(str(path)):
                continue
            if any(part in ('__pycache__', '.git') for part in path.parts):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b'\0' in raw[:4096]:          # a binary artifact, not configuration
                continue
            text = raw.decode('utf-8', errors='ignore')
            for line_no, line in enumerate(text.splitlines(), 1):
                if ALLOWED.search(line):
                    continue
                m = sibling.search(line)
                if m:
                    offenders.setdefault(m.group(1), []).append(
                        f'{path.relative_to(work)}:{line_no}')
    if offenders:
        for other, where in sorted(offenders.items()):
            fail('self-contained', f'references {other} at {", ".join(where[:4])}'
                                   + (f' and {len(where)-4} more' if len(where) > 4 else ''))
    else:
        print('  OK   references no other suite')

    # ---------------------------------------------------------------- 2. origin
    flow = work / 'flow.cylc'
    origin = None
    if flow.is_file():
        for line in flow.read_text().splitlines():
            m = re.match(r'\s*origin\s*=\s*(\S+)', line)
            if m:
                origin = m.group(1)
                break
    if origin is None:
        note('no origin setting found in flow.cylc')
    elif Path(origin).resolve() != work.resolve():
        fail('origin', f'flow.cylc origin is {origin}, expected {work}')
    else:
        print('  OK   origin points at this suite')

    # ---------------------------------------------------------------- 3. work directories
    naming = read_csh(auto / 'naming.csh')
    stray = sorted({v for k, v in naming.items()
                    if k.endswith('WorkDir') and '/pandac/' in v and f'/pandac/{suite}/' not in v})
    if stray:
        fail('work directories', f'point outside this suite: {", ".join(stray[:3])}')
    elif naming:
        print(f'  OK   all {sum(1 for k in naming if k.endswith("WorkDir"))} work directories are inside this suite')

    # ---------------------------------------------------------------- 4. scenario agreement
    scenario = args.scenario or read_csh(auto / 'scenario.csh').get('scenarioConfig')
    if not scenario or not Path(scenario).is_file():
        fail('scenario', f'scenario file not found: {scenario}')
    else:
        print(f'scenario {scenario}')
        want_obs, want_mon = scenario_observers(scenario)
        variational = read_csh(auto / 'variational.csh')
        have_obs = variational.get('observers', '').strip('()').split()
        have_mon = variational.get('monitors', '').strip('()').split()
        if want_obs and have_obs != want_obs:
            fail('observers', f'config/auto has {have_obs}, scenario has {want_obs}')
        elif want_obs:
            print(f'  OK   observers match the scenario: {" ".join(have_obs)}')
        if want_mon and have_mon != want_mon:
            fail('monitors', f'config/auto has {have_mon}, scenario has {want_mon}')

        first = scenario_value(scenario, 'first cycle point')
        restart = scenario_value(scenario, 'restart cycle point')
        final = scenario_value(scenario, 'final cycle point')
        if flow.is_file():
            text = flow.read_text()
            m_init = re.search(r'initial cycle point\s*=\s*(\S+)', text)
            m_fin = re.search(r'final cycle point\s*=\s*(\S+)', text)
            flow_init = m_init.group(1) if m_init else None
            flow_final = m_fin.group(1) if m_fin else None
            expected = restart or first
            if expected and flow_init and flow_init != expected:
                fail('initial cycle', f'flow.cylc starts at {flow_init}, scenario says {expected}'
                                      + (f' (first cycle point is {first})' if first != expected else ''))
            elif flow_init:
                print(f'  OK   initial cycle point {flow_init} matches the scenario')
            if final and flow_final and flow_final != final:
                fail('final cycle', f'flow.cylc ends at {flow_final}, scenario says {final}')

    # ---------------------------------------------------------------- 5. observer plugs and inputs
    variational = read_csh(auto / 'variational.csh')
    observers = variational.get('observers', '').strip('()').split()
    base = work / 'config' / 'jedi' / 'ObsPlugs' / 'da' / 'base'
    filters = work / 'config' / 'jedi' / 'ObsPlugs' / 'da' / 'filters'
    observations = work / 'scenarios' / 'defaults' / 'observations.yaml'
    registry = observations.read_text() if observations.is_file() else ''
    for observer in observers:
        missing = [d.name for d in (base, filters) if not (d / f'{observer}.yaml').is_file()]
        if missing:
            fail('plugs', f'{observer}: no plug in {", ".join(missing)}')
            continue
        m = re.search(rf'^\s*{re.escape(observer)}:\s*(/\S+)\s*(?:#.*)?$', registry, re.M)
        if not m:
            fail('registry', f'{observer}: no IODADirectory entry in observations.yaml')
        elif not Path(m.group(1)).is_dir():
            fail('registry', f'{observer}: IODA directory does not exist: {m.group(1)}')
        # the file name the plug reads must carry the observer name
        plug = (base / f'{observer}.yaml').read_text()
        m_file = re.search(r'obsfile:\s*\{\{InDBDir\}\}/(\S+?)_obs_', plug)
        if m_file and m_file.group(1) != observer:
            fail('file name', f'{observer}: base plug reads {m_file.group(1)}_obs_*.h5')
    if observers and not any(f[0] in ('plugs', 'registry', 'file name') for f in failures):
        print(f'  OK   all {len(observers)} observers have plugs, a registered directory and a matching file name')

    # ---------------------------------------------------------------- report
    print()
    for text in notes:
        print(f'  note {text}')
    if failures:
        print(f'{len(failures)} problem(s):')
        for check, detail in failures:
            print(f'  FAIL {check}: {detail}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
