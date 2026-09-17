"""Cache filenames carry no fingerprint, so the stored attributes must catch
every case the old hashed names used to make impossible by construction."""
from pathlib import Path
import tempfile

import numpy as np
from scipy.io import netcdf_file

from mpas_emissions.cache_paths import (
    CacheIdentityError, grid_dims_tag, resolve_grid_name,
    gridspec_file, scrip_file, weights_file,
    cache_is_usable, require_cache_identity, stamp_nc_attrs, read_nc_attrs,
)
from mpas_emissions.regular_grid import regular_grid_fingerprint, write_gridspec_from_centers


def _stub_weights(path: Path, **attrs):
    with netcdf_file(path, 'w', version=2) as ds:
        ds.createDimension('n_s', 1)
        v = ds.createVariable('S', 'd', ('n_s',)); v[:] = [1.0]
        for k, val in attrs.items():
            setattr(ds, k, str(val).encode())


def test_names_are_readable_and_shared_across_inventories():
    """One 0.1-degree weight file serves CEDS, GFAS and QFED alike."""
    lat = np.linspace(-89.95, 89.95, 1800)
    lon = np.linspace(-179.95, 179.95, 3600)
    cache = Path('/cache')
    name = resolve_grid_name('x1.163842', 163842)
    assert scrip_file(cache, name).name == 'mpas_scrip_x1.163842.nc'
    assert weights_file(cache, grid_dims_tag(lat, lon), name).name == \
        'weights_1800x3600_to_x1.163842.nc'
    # CAMS 0.75-degree lands on a different name, so the two never collide.
    clat = np.linspace(-90.0, 90.0, 241); clon = np.linspace(-180.0, 179.25, 480)
    assert weights_file(cache, grid_dims_tag(clat, clon), name).name == \
        'weights_241x480_to_x1.163842.nc'


def test_gridspec_roundtrip_matches_its_own_fingerprint():
    lat = np.linspace(-89.5, 89.5, 180)
    lon = np.linspace(-179.5, 179.5, 360)
    with tempfile.TemporaryDirectory() as d:
        gs = gridspec_file(Path(d), grid_dims_tag(lat, lon))
        write_gridspec_from_centers(lat, lon, gs)
        tag = regular_grid_fingerprint(lat, lon)
        assert read_nc_attrs(gs, ['grid_fingerprint'])['grid_fingerprint'] == tag
        assert cache_is_usable(gs, {'grid_fingerprint': tag})


def test_same_shape_different_grid_is_rejected():
    """The case the filename can no longer distinguish: equal dimensions,
    different coordinates. Both are 180x360, so both want gridspec_180x360.nc."""
    lat = np.linspace(-89.5, 89.5, 180); lon = np.linspace(-179.5, 179.5, 360)
    shifted_lon = lon + 0.25
    with tempfile.TemporaryDirectory() as d:
        gs = gridspec_file(Path(d), grid_dims_tag(lat, lon))
        write_gridspec_from_centers(lat, lon, gs)
        assert gs == gridspec_file(Path(d), grid_dims_tag(lat, shifted_lon))
        assert not cache_is_usable(gs, {'grid_fingerprint': regular_grid_fingerprint(lat, shifted_lon)})


def test_stale_mesh_weight_cache_is_regenerated_not_reused():
    with tempfile.TemporaryDirectory() as d:
        wf = weights_file(Path(d), '1800x3600', 'x1.163842')
        _stub_weights(wf, grid_fingerprint='aaaa', mesh_fingerprint='old_mesh')
        assert not cache_is_usable(wf, {'grid_fingerprint': 'aaaa', 'mesh_fingerprint': 'new_mesh'})
        assert cache_is_usable(wf, {'grid_fingerprint': 'aaaa', 'mesh_fingerprint': 'old_mesh'})


def test_supplied_weight_file_for_another_mesh_raises():
    """A configured --weights file is fatal on mismatch, never silently used."""
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / 'hand_built.nc'
        _stub_weights(wf, grid_fingerprint='aaaa', mesh_fingerprint='some_other_mesh')
        try:
            require_cache_identity(wf, {'grid_fingerprint': 'aaaa', 'mesh_fingerprint': 'this_mesh'})
        except CacheIdentityError as exc:
            assert 'some_other_mesh' in str(exc) and 'this_mesh' in str(exc)
        else:
            raise AssertionError('a weight file built for another mesh was accepted')


def test_unstamped_legacy_file_is_accepted_with_a_warning():
    """Pre-existing hand-built weights carry no attributes; they must keep working."""
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / 'legacy.nc'
        _stub_weights(wf)
        require_cache_identity(wf, {'grid_fingerprint': 'aaaa', 'mesh_fingerprint': 'bbbb'})
        assert cache_is_usable(wf, {'grid_fingerprint': 'aaaa', 'mesh_fingerprint': 'bbbb'})


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn(); print(f'{name} passed')


if __name__ == '__main__':
    main()
