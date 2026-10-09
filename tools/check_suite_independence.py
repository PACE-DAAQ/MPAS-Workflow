#!/usr/bin/env python
"""Check that a staged suite is self-contained and matches its scenario, before it is installed.

Why this exists
---------------
A suite is normally generated from a scenario yaml. When one is instead created by COPYING another
suite's tree and hand-editing the generated files, four faults slip through silently, and all four
happened on 2026-10-09 while staging syha_r4_qfed_polar_new:

  1. `origin` in flow.cylc still named the source suite. It is the directory every task cds into, so
     the new suite ran using the SOURCE suite's entire configuration and wrote into its work
     directories -- while the new suite's own config sat unused.
  2. flow.cylc carried the source suite's RESUMED initial cycle point rather than the scenario's
     first cycle point, so the run would have started mid-period.
  3. The generated config/auto files were edited but the scenario yaml was not, so the suite and the
     thing that regenerates it disagreed. A later Run.py would have silently restored the old
     observers.
  4. A plug copied from before 2026-09-28 still carried `select_mean: true`. Superobbing rejects
     every superob here, so that observer assimilated nothing while every other check passed.

check_experiment_config.py compares the science configuration of two experiments. This is the
complementary check: that ONE suite is internally consistent and references nothing outside itself.

Every check FAILS when the thing it is supposed to compare is absent, not just when it disagrees.
A silent skip is how all four faults above survived the staging in the first place.

Usage
  check_suite_independence.py <experimentDir> [--scenario <scenario.yaml>]

<experimentDir> holds MPAS-Workflow/, e.g. /glade/derecho/scratch/$USER/pandac/<SuiteName>.
The scenario is read from config/auto/scenario.csh when not given.

Exit status
  0  every check passed
  1  the suite is faulty -- do not install it
  2  the check could NOT be run (no config/auto, no yaml module). Not a verdict on the suite.

submit.csh gates `cylc install` on this and aborts on 1 only, so a missing dependency cannot block
an otherwise sound suite. Set skipSuiteIndependenceCheck to bypass the gate deliberately.
"""
import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:                     # exit 2: cannot check, which is not a verdict on the suite
    sys.stderr.write('check_suite_independence: no yaml module available, cannot check\n')
    sys.exit(2)

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


def load_scenario(scenario_path):
    """Parse the scenario with the same yaml loader the workflow itself uses.

    initialize/config/Config.py reads these files with yaml, so anything it accepts must be
    accepted here: block sequences, flow-style lists (`observers: ['a', 'b']`) and bracketed
    multiline lists all have to parse. A hand-rolled line reader only handled the dash form and
    returned an empty list for the others, which silently skipped the agreement check.
    """
    with open(scenario_path) as handle:
        return yaml.safe_load(handle)


def scenario_lists(scenario):
    """The ASSIMILATED observers and monitors.

    Taken from the `variational` mapping only. A scenario also carries an `hofx:` section with its
    own observers list; merging the two would report a mismatch against config/auto that is not
    real, because config/auto/variational.csh describes the variational stream alone.
    """
    variational = (scenario or {}).get('variational') or {}
    observers = variational.get('observers') or []
    monitors = variational.get('monitors') or []
    return [str(o) for o in observers], [str(m) for m in monitors]


def workflow_value(scenario, key):
    workflow = (scenario or {}).get('workflow') or {}
    value = workflow.get(key)
    return None if value is None else str(value)


def check_cycle_point(label, expected, found, extra=''):
    """Compare one cycle point. Absence of either side is a failure, not a skip."""
    if expected is None:
        return
    if found is None:
        fail(label, f'flow.cylc declares no {label}, scenario says {expected}')
    elif found != expected:
        fail(label, f'flow.cylc has {found}, scenario says {expected}{extra}')
    else:
        print(f'  OK   {label} {found} matches the scenario')


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
        sys.stderr.write(f'FATAL: no config/auto under {work}\n')
        sys.exit(2)                     # cannot check; see the exit-status convention above
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
    # Every generated task runs `cd $origin/` (initialize/suites/SuiteBase.py), so a missing or
    # malformed origin is as damaging as a wrong one: it must not pass.
    flow = work / 'flow.cylc'
    origin = None
    if not flow.is_file():
        fail('origin', f'no flow.cylc in {work}')
    else:
        for line in flow.read_text().splitlines():
            m = re.match(r'\s*origin\s*=\s*(\S+)', line)
            if m:
                origin = m.group(1)
                break
        if origin is None:
            fail('origin', 'flow.cylc declares no origin; every task cds into it')
        elif Path(origin).resolve() != work.resolve():
            fail('origin', f'flow.cylc origin is {origin}, expected {work}')
        else:
            print('  OK   origin points at this suite')

    # ---------------------------------------------------------------- 3. work directories
    # Resolve every *WorkDir and require it to sit under this suite's own root, so a stray path
    # anywhere on the filesystem is caught -- not only one that happens to contain '/pandac/'.
    # resolve() also collapses '..', so traversal cannot escape the comparison.
    roots = {exp.resolve()}
    if origin:
        # in installed-copy mode the work directories belong to the source experiment, which is
        # the parent of origin (<experiment>/MPAS-Workflow); derive it rather than guessing.
        roots.add(Path(origin).resolve().parent)
    naming = read_csh(auto / 'naming.csh')
    work_dirs = {k: v for k, v in naming.items() if k.endswith('WorkDir')}
    stray, unresolved = [], []
    for key, value in sorted(work_dirs.items()):
        if '$' in value or not value.startswith('/'):
            unresolved.append(f'{key}={value}')      # an unexpanded csh variable; cannot compare
            continue
        if ALLOWED.search(value):
            continue
        resolved = Path(value).resolve()
        if not any(resolved == root or root in resolved.parents for root in roots):
            stray.append(f'{key}={value}')
    if not work_dirs:
        fail('work directories', f'no *WorkDir settings found in {auto / "naming.csh"}')
    elif stray:
        fail('work directories', f'{len(stray)} point outside this suite: {", ".join(stray[:3])}'
                                 + (f' and {len(stray)-3} more' if len(stray) > 3 else ''))
    else:
        print(f'  OK   all {len(work_dirs)} work directories are inside this suite')
    for text in unresolved:
        note(f'work directory not expanded, cannot verify: {text}')

    # ---------------------------------------------------------------- 4. scenario agreement
    scenario_path = args.scenario or read_csh(auto / 'scenario.csh').get('scenarioConfig')
    if not scenario_path or not Path(scenario_path).is_file():
        fail('scenario', f'scenario file not found: {scenario_path}')
    else:
        print(f'scenario {scenario_path}')
        try:
            scenario = load_scenario(scenario_path)
        except yaml.YAMLError as error:
            fail('scenario', f'{scenario_path} does not parse: {error}')
            scenario = None
        want_obs, want_mon = scenario_lists(scenario)
        variational = read_csh(auto / 'variational.csh')
        have_obs = variational.get('observers', '').strip('()').split()
        have_mon = variational.get('monitors', '').strip('()').split()
        if not want_obs:
            fail('observers', f'{scenario_path} declares no variational observers')
        elif have_obs != want_obs:
            fail('observers', f'config/auto has {have_obs}, scenario has {want_obs}')
        else:
            print(f'  OK   observers match the scenario: {" ".join(have_obs)}')
        if want_mon and have_mon != want_mon:
            fail('monitors', f'config/auto has {have_mon}, scenario has {want_mon}')

        first = workflow_value(scenario, 'first cycle point')
        restart = workflow_value(scenario, 'restart cycle point')
        final = workflow_value(scenario, 'final cycle point')
        if flow.is_file():
            text = flow.read_text()
            m_init = re.search(r'initial cycle point\s*=\s*(\S+)', text)
            m_fin = re.search(r'final cycle point\s*=\s*(\S+)', text)
            expected_init = restart or first
            check_cycle_point(
                'initial cycle point', expected_init,
                m_init.group(1) if m_init else None,
                f' (first cycle point is {first})' if first != expected_init else '')
            check_cycle_point('final cycle point', final,
                              m_fin.group(1) if m_fin else None)

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
        # The file name the plug reads must carry the observer name. A plug with NO obsfile line of
        # this form is also a failure: PrepJEDI would stage nothing and the obs space would be
        # empty, which is the quiet way an observer disappears from a run.
        plug = (base / f'{observer}.yaml').read_text()
        m_file = re.search(r'obsfile:\s*\{\{InDBDir\}\}/(\S+?)_obs_', plug)
        if not m_file:
            fail('file name', f'{observer}: base plug declares no '
                              '"obsfile: {{InDBDir}}/<name>_obs_..." input')
        elif m_file.group(1) != observer:
            fail('file name', f'{observer}: base plug reads {m_file.group(1)}_obs_*.h5')

    # ---------------------------------------------------------------- 6. superobbing
    # select_mean: true has never worked in this project: it rejects every superob, because the
    # reduced obs space carries missing values that the next Bounds Check flags. A plug copied from
    # before 2026-09-28 still carries it, and the suite then assimilates nothing from that observer
    # while every other check passes. syha_r4_qfed_polar_new hit exactly this on 2026-10-09.
    for observer in observers:
        plug = filters / f'{observer}.yaml'
        if not plug.is_file():
            continue
        for line in plug.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if re.match(r'select_mean:\s*true\b', stripped):
                fail('superobbing', f'{observer}: select_mean is true; it rejects every superob')

    if not observers:
        fail('observers', f'config/auto/variational.csh lists no observers')
    elif not any(f[0] in ('plugs', 'registry', 'file name') for f in failures):
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
