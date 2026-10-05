"""copy_mpas_vars.py must clip negative water vapour, hydrometeors and aerosol scalars."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from netCDF4 import Dataset

root = Path(__file__).resolve().parents[2]
script = root / 'tools/copy_mpas_vars.py'

assignments = {}
for node in ast.parse(script.read_text()).body:
    is_assign = isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    if is_assign and node.targets[0].id in ['vars_to_copy', 'hydrometeor_vars', 'number_concentration_vars', 'background_chemistry_vars']:
        assignments[node.targets[0].id] = ast.literal_eval(node.value)

aerosols = assignments['vars_to_copy']
hydrometeors = sorted(set(assignments['hydrometeor_vars'] + assignments['number_concentration_vars']))
backgrounds = assignments['background_chemistry_vars']
numbers = assignments['number_concentration_vars']
clipped_names = ['qv'] + hydrometeors + aerosols + backgrounds
untouched_name = 'theta'


def write_file(path, value_by_name):
    """Two-cell file; each variable gets [value, 5.0]."""
    with Dataset(path, 'w', format='NETCDF3_64BIT_DATA') as dataset:
        dataset.createDimension('Time', 1)
        dataset.createDimension('nCells', 2)
        for name, value in value_by_name.items():
            dataset.createVariable(name, 'f4', ('Time', 'nCells'))[:] = [[value, 5.0]]


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # Source (analysis/forecast) carries negative aerosols and hydrometeors; the cold IC
    # carries a negative qv and a negative theta that must NOT be clipped.
    source_values = {name: -3.0 for name in aerosols + hydrometeors}
    cold_values = {name: 2.0 for name in aerosols + hydrometeors}
    # The number concentrations are never copied from the source, so the cold IC
    # must hold the negative values for the clip to be exercised.
    cold_values.update({name: -2.0 for name in numbers})
    cold_values.update({name: -1.0 for name in backgrounds})
    cold_values['qv'] = -0.25
    cold_values[untouched_name] = -7.0
    write_file(tmp / 'source.nc', source_values)
    write_file(tmp / 'cold.nc', cold_values)
    destination = tmp / 'out.nc'
    destination.write_bytes((tmp / 'cold.nc').read_bytes())
    result = subprocess.run(
        [sys.executable, str(script), '--include-hydrometeors', str(tmp / 'source.nc'), str(destination)],
        check=True, capture_output=True, text=True)
    with Dataset(destination) as dataset:
        for name in clipped_names:
            values = dataset[name][:]
            assert values.min() >= 0, f'{name} still negative: {values}'
        assert dataset['qv'][0, 0] == 0.0 and dataset['qv'][0, 1] == 5.0
        for name in backgrounds:
            assert dataset[name][0, 0] == 0.0, name
        for name in numbers:
            assert dataset[name][0, 0] == 0.0 and dataset[name][0, 1] == 5.0, name
        for name in aerosols:
            if name == 'persistent_hno3':
                continue
            assert dataset[name][0, 0] == 0.0 and dataset[name][0, 1] == 5.0, name
        assert dataset[untouched_name][0, 0] == -7.0, 'non-scalar field must not be clipped'
    assert 'clipped qv: 1 values' in result.stdout, result.stdout
    assert 'Non-negativity clip: set' in result.stdout

    # Clean input: nothing to clip, and the log says so.
    write_file(tmp / 'clean_source.nc', {name: 4.0 for name in aerosols + hydrometeors})
    write_file(tmp / 'clean_cold.nc', {**{name: 2.0 for name in aerosols + hydrometeors + backgrounds}, 'qv': 0.01})
    clean_destination = tmp / 'clean_out.nc'
    clean_destination.write_bytes((tmp / 'clean_cold.nc').read_bytes())
    clean = subprocess.run(
        [sys.executable, str(script), '--include-hydrometeors', str(tmp / 'clean_source.nc'), str(clean_destination)],
        check=True, capture_output=True, text=True)
    assert 'no negative scalar values found' in clean.stdout, clean.stdout

    # Reset mode: persistent_hno3 is not carried, so the cold IC's negative value
    # would reach the forecast unless it is clipped independently.
    reset_cold_values = {name: 2.0 for name in aerosols + hydrometeors + backgrounds}
    reset_cold_values.update({'qv': 0.01, 'persistent_hno3': -1.0})
    write_file(tmp / 'reset_cold.nc', reset_cold_values)
    reset_destination = tmp / 'reset_out.nc'
    reset_destination.write_bytes((tmp / 'reset_cold.nc').read_bytes())
    reset = subprocess.run(
        [sys.executable, str(script), '--include-hydrometeors', '--reset-hno3',
         str(tmp / 'source.nc'), str(reset_destination)],
        check=True, capture_output=True, text=True)
    with Dataset(reset_destination) as dataset:
        assert dataset['persistent_hno3'][0, 0] == 0.0 and dataset['persistent_hno3'][0, 1] == 5.0
    assert 'clipped persistent_hno3: 1 values' in reset.stdout, reset.stdout

print('PASS: negative qv, hydrometeors and all aerosol scalars are clipped at zero; other fields untouched')
