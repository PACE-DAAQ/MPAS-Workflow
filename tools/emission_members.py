#!/usr/bin/env python3
"""Validate inventory/online-factor routing before rendering forecast inputs."""
import argparse
import csv
import json
import math
from pathlib import Path
import re


def factor(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError('emission factors must be finite and nonnegative')
    return result


def read_members(path, count):
    with open(path, newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != count or [int(r['member']) for r in rows] != list(range(1, count + 1)):
        raise ValueError('member table must contain exactly members 1 through ensembleforecast.n in order')
    for row in rows:
        for key, allowed in [('anth', {'cams', 'ceds', 'cams-mix'}),
                             ('biob', {'finn', 'gfas', 'qfed', 'gbbepx'}), ('biog', {'cams'})]:
            if row[key] not in allowed:
                raise ValueError(f'unsupported {key}: {row[key]}')
        for key in ['dust_relative', 'seasalt_relative']:
            row[key] = factor(row[key])
    return rows


def render_namelist(path, dust, seasalt):
    p = Path(path)
    content = p.read_text()
    for name, value in [('dust', dust), ('seasalt', seasalt)]:
        key = f'config_gocart2G_{name}_emission_factor'
        pattern = rf'(?im)^\s*{key}\s*=.*$'
        line = f'    {key} = {factor(value):.17g}'
        if re.search(pattern, content):
            content = re.sub(pattern, line, content)
        else:
            content, n = re.subn(r'(?im)^\s*&chemistry\s*$', lambda m: m[0] + '\n' + line, content)
            if n != 1:
                raise ValueError('expected exactly one &chemistry namelist group')
    p.write_text(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table', default='')
    parser.add_argument('--count', type=int, default=0)
    parser.add_argument('--member', type=int, default=1)
    parser.add_argument('--role', choices=['central', 'ensemble'], default='central')
    parser.add_argument('--dust', required=True)
    parser.add_argument('--seasalt', required=True)
    parser.add_argument('--namelist')
    parser.add_argument('--csh')
    args = parser.parse_args()
    dust, seasalt = factor(args.dust), factor(args.seasalt)
    row = None
    if args.role == 'ensemble' and args.table:
        rows = read_members(args.table, args.count)
        if not 1 <= args.member <= len(rows):
            raise ValueError('member index outside configured table')
        row = rows[args.member - 1]
        dust *= row['dust_relative']
        seasalt *= row['seasalt_relative']
    factor(dust)
    factor(seasalt)
    if args.csh:
        lines = [f'set selectedDustFactor = {dust:.17g}', f'set selectedSeasaltFactor = {seasalt:.17g}']
        if row is not None:
            lines += [f'set {key}Emissions = {row[key]}' for key in ['anth', 'biob', 'biog']]
        Path(args.csh).write_text('\n'.join(lines) + '\n')
        Path(args.csh + '.json').write_text(json.dumps(dict(role=args.role, member=args.member,
            table=args.table, inventories=row, dust_factor=dust, seasalt_factor=seasalt), indent=2))
    if args.namelist:
        render_namelist(args.namelist, dust, seasalt)


if __name__ == '__main__':
    main()
