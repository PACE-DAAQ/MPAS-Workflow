from pathlib import Path
import tempfile
from scipy.io import netcdf_file
import numpy as np
from mpas_emissions.sparse_weights import SparseWeights
from mpas_emissions.regular_grid import regular_grid_fingerprint


def check_grid_fingerprint_ignores_encoding_noise():
    """A provider re-encoding identical centers must not read as a new grid.

    QFED is the live case: files through 2024-09-10 store float32-rounded
    centers in a float64 variable (lat[0] = -89.94999695) while 2024-09-11
    onward store true float64 (-89.95). The grids agree to ~6e-6 deg, but
    hashing the raw bytes split them in two, which halved the weight cache and
    made process() reject the series with "source grid changes across files".
    """
    lat = np.linspace(-89.95, 89.95, 1800)
    lon = np.linspace(-179.95, 179.95, 3600)
    base = regular_grid_fingerprint(lat, lon)

    # A float32 round-trip reproduces exactly the archive's encoding change.
    lat32 = np.asarray(lat, dtype=np.float32).astype(np.float64)
    lon32 = np.asarray(lon, dtype=np.float32).astype(np.float64)
    assert np.max(np.abs(lon32 - lon)) > 1.0e-6, 'test lost the encoding difference'
    assert regular_grid_fingerprint(lat32, lon32) == base

    # Genuinely different grids must still be rejected.
    assert regular_grid_fingerprint(lat[::2], lon[::2]) != base   # 0.2 deg
    assert regular_grid_fingerprint(lat + 0.05, lon) != base      # half-cell shift
    assert regular_grid_fingerprint(lat + 1.0e-3, lon) != base    # 1e-3 deg shift
    print('grid-fingerprint encoding-noise test passed')


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
    check_grid_fingerprint_ignores_encoding_noise()

if __name__=='__main__': main()
