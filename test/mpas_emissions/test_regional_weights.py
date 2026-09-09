from pathlib import Path
import tempfile
from scipy.io import netcdf_file
from mpas_emissions.sparse_weights import SparseWeights


def main():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'regional.nc'
        with netcdf_file(p,'w',version=2) as ds:
            ds.createDimension('n_s',3)
            S=ds.createVariable('S','d',('n_s',)); S[:] = [0.5,0.5,1.0]
            row=ds.createVariable('row','i',('n_s',)); row[:] = [1,1,2]
            col=ds.createVariable('col','i',('n_s',)); col[:] = [1,2,3]
        w=SparseWeights.open(p,n_dest=2,n_src=4,require_full_destination=True,require_full_source=False)
        assert w.n_src == 4
        try:
            SparseWeights.open(p,n_dest=2,n_src=4,require_full_destination=True,require_full_source=True)
        except ValueError as e:
            assert 'source cells' in str(e)
        else:
            raise AssertionError('global source-coverage check should fail')
        print('regional source-coverage test passed')

if __name__=='__main__': main()
