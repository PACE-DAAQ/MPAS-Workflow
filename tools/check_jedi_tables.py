#!/usr/bin/env python3
"""Validate explicitly configured MPAS RRTMG tables before launching JEDI MPI.

Run in the same working directory as the application. This checks availability,
not binary compatibility or the complete physics input contract.
"""
import argparse
from pathlib import Path
import re
import sys
import yaml


def namelists(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'nml_file':
                yield Path(child)
            else:
                yield from namelists(child)
    elif isinstance(value, list):
        for child in value:
            yield from namelists(child)


def check(config, directory=Path('.')):
    directory = Path(directory)
    with Path(config).open() as stream:
        text = stream.read()
        # Legacy workflow templates use an unquoted %member% pattern accepted
        # by eckit; quote that scalar for PyYAML without changing the run file.
        text = re.sub(r"(?m)^(\s*pattern:\s*)(%[A-Za-z0-9_]+%)(\s*)$", r"\1'\2'\3", text)
        data = yaml.safe_load(text)
    paths = set(namelists(data))
    if not paths:
        raise ValueError('No geometry nml_file found in JEDI configuration')
    required = set()
    for path in paths:
        path = path if path.is_absolute() else directory/path
        text = path.read_text()
        # Match explicit scheme assignments, ignoring commented-out settings.
        text = '\n'.join(line.split('!', 1)[0] for line in text.splitlines())
        for band in ('sw', 'lw'):
            match = re.search(r'\bconfig_radt_'+band+r'_scheme\s*=\s*([\'"])(.*?)\1', text, re.I)
            if match and 'rrtmg' in match.group(2).lower():
                required.add('RRTMG_'+band.upper()+'_DATA')
    errors = []
    for name in sorted(required):
        path = directory/name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError('missing, broken link, non-file, or empty')
            with path.open('rb') as stream:
                if not stream.read(1):
                    raise OSError('empty')
        except OSError as exc:
            errors.append(f'{path}: {exc}')
    if errors:
        raise ValueError('Required MPAS radiation tables are unusable:\n'+'\n'.join(errors))
    return sorted(required)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    args = parser.parse_args()
    try:
        required = check(args.config)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print('JEDI table preflight failed: '+str(exc), file=sys.stderr)
        return 1
    print('JEDI table preflight passed: '+(', '.join(required) or 'no explicit RRTMG scheme'))
    return 0

if __name__ == '__main__':
    sys.exit(main())
