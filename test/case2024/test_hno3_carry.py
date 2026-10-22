import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import numpy as np
from netCDF4 import Dataset
root=Path(__file__).resolve().parents[2]
script=root/'tools/copy_mpas_vars.py'
assignments={}
for node in ast.parse(script.read_text()).body:
    if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ['vars_to_copy','land_vars']:
        assignments[node.targets[0].id]=ast.literal_eval(node.value)
variables=assignments['vars_to_copy']+assignments['land_vars']+['background_hno3']
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    for filename,value in [('source.nc',7),('cold.nc',2)]:
        with Dataset(tmp/filename,'w',format='NETCDF3_64BIT_DATA') as ds:
            ds.createDimension('Time',1);ds.createDimension('nCells',2)
            for name in variables: ds.createVariable(name,'f4',('Time','nCells'))[:]=value
    for mode in ['carry','reset']:
        dest=tmp/f'{mode}.nc';shutil.copyfile(tmp/'cold.nc',dest)
        flags=['--include-land']+(['--reset-hno3'] if mode=='reset' else [])
        subprocess.run([sys.executable,str(script),*flags,str(tmp/'source.nc'),str(dest)],check=True,stdout=subprocess.DEVNULL)
    with Dataset(tmp/'carry.nc') as carry,Dataset(tmp/'reset.nc') as reset:
        for name in variables:
            if name=='persistent_hno3':
                assert np.all(carry[name][:]==7) and np.all(reset[name][:]==2)
            elif name=='background_hno3':
                assert np.all(carry[name][:]==2) and np.all(reset[name][:]==2)
            else:
                assert np.all(carry[name][:]==7) and np.array_equal(carry[name][:],reset[name][:]),name
print('PASS: only persistent_hno3 differs; all aerosol and six land fields identical; prescribed background retained')
# Exercise the JEDI handoff: analyzed tracers must come from the analysis,
# but unanalysed precursors, persistent HNO3, and soil/snow from the prior member.
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    for filename,value in [('analysis.nc',7),('prior.nc',11),('cold.nc',2)]:
        with Dataset(tmp/filename,'w',format='NETCDF3_64BIT_DATA') as ds:
            ds.createDimension('Time',1);ds.createDimension('nCells',2)
            for name in variables: ds.createVariable(name,'f4',('Time','nCells'))[:]=value
    for mode in ['carry','reset']:
        dest=tmp/f'{mode}.nc';shutil.copyfile(tmp/'cold.nc',dest)
        flags=['--include-land','--aux-source',str(tmp/'prior.nc')]+(['--reset-hno3'] if mode=='reset' else [])
        subprocess.run([sys.executable,str(script),*flags,str(tmp/'analysis.nc'),str(dest)],check=True,stdout=subprocess.DEVNULL)
        with Dataset(dest) as ds:
            assert np.all(ds['qdust1'][:]==7)
            assert np.all(ds['qso2'][:]==11)
            assert np.all(ds['persistent_hno3'][:]==(11 if mode=='carry' else 2))
            for name in assignments['land_vars']: assert np.all(ds[name][:]==11),name
print('PASS: auxiliary carry source preserves each member while analyzed dust comes from JEDI')
