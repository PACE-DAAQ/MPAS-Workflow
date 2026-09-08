"""Regressions for defects found in the PR #13 review.

Each test pins behavior that was previously wrong in a way that produced silent
bad data or an unconditional task failure rather than an obvious error.
"""
import gzip
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

import mpas_emissions.finn_cli as fc
import mpas_emissions.io as mio
from mpas_emissions.time_axis import SourceRecord, resolve_brackets


def _monthly_targets(records, start, end):
    """Mirror of the monthly branch of regular_inventory.process_config."""
    months = []
    t = datetime(start.year, start.month, 1)
    while t <= end:
        months.append((t.year, t.month))
        t = datetime(t.year + (1 if t.month == 12 else 0),
                     1 if t.month == 12 else t.month + 1, 1)
    by_month = {}
    for r in records:
        by_month.setdefault((r.valid_time.year, r.valid_time.month), []).append(r.valid_time)
    if months and all(len(by_month.get(m, ())) == 1 for m in months):
        return [by_month[m][0] for m in months if start <= by_month[m][0] <= end]
    return [datetime(y, mo, 1) for (y, mo) in months if datetime(y, mo, 1) >= start]


def test_monthly_targets_bracket_mid_month_sources():
    # CEDS timestamps mid-month; day-1 targets could never be bracketed.
    start, end = datetime(2024, 1, 1), datetime(2024, 12, 31, 23)
    recs = [SourceRecord(datetime(2024, m, 16), f'f{m}.nc', 0) for m in range(1, 13)]
    targets = _monthly_targets(recs, start, end)
    assert len(targets) == 12
    brackets = resolve_brackets(recs, targets, method='linear', max_gap=None,
                                allow_extrapolation=False)
    assert len(brackets) == 12
    # every target lands exactly on its own record, so no interpolation is needed
    assert all(b.before.path == b.after.path for b in brackets)


def test_monthly_targets_unchanged_for_first_of_month_sources():
    start, end = datetime(2024, 1, 1), datetime(2024, 12, 31, 23)
    recs = [SourceRecord(datetime(2024, m, 1), f'g{m}.nc', 0) for m in range(1, 13)]
    assert _monthly_targets(recs, start, end) == [datetime(2024, m, 1) for m in range(1, 13)]


def _write_container(path, days):
    with gzip.open(path, 'wt') as fh:
        fh.write("DAY,TIME,GENVEG,LATI,LONGI,AREA,CO\n")
        for d in days:
            fh.write(f"{d},1200,2,35.0,-100.0,1.0e6,10.0\n")


def test_read_finn_csv_accepts_chunksize():
    # pandas returns a TextFileReader, not a DataFrame, when chunksize is given.
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, 'FINNv2.5_modvrs_MOZART_2021_c20220714.txt.gz')
        _write_container(f, list(range(1, 32)))
        rows = 0
        for chunk in fc._read_finn_csv(f, chunksize=10):
            assert list(chunk.columns)[:2] == ['DAY', 'TIME']
            rows += len(chunk)
        assert rows == 31


def test_annual_container_day_not_dated_from_creation_stamp():
    # '..._2021_c20220714' must not be read as YYYYMM=2022-07.
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, 'FINNv2.5_modvrs_MOZART_2021_c20220714.txt.gz')
        _write_container(f, list(range(1, 32)))
        df = fc._read_finn_csv(f)
        dates = fc._parse_container_dates(
            df['DAY'], f, {'annual_date_column': ['DAY'], 'annual_day_mode': 'auto'})
        assert str(dates.iloc[0].date()) == '2021-01-01'
        assert str(dates.iloc[30].date()) == '2021-01-31'


def test_monthly_container_dating_still_correct():
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, 'FINNv2.5_modvrs_MOZART_202202_c20230101.txt.gz')
        _write_container(f, list(range(32, 60)))
        df = fc._read_finn_csv(f)
        dates = fc._parse_container_dates(
            df['DAY'], f, {'annual_date_column': ['DAY'], 'annual_day_mode': 'auto'})
        assert str(dates.iloc[0].date()) == '2022-02-01'


def test_interrupted_emission_write_leaves_no_partial_file():
    # A partial file reads back as NC_FILL_FLOAT (9.97e36) and --reuse-existing
    # would accept it, so MPAS would ingest fill values as emission fluxes.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'partial.nc'
        times = [datetime(2024, m, 1) for m in range(1, 13)]
        fields = {'nh3_anth_sum': np.full((12, 4), 2.0, dtype='f4')}
        real = mio._write_mpas_emissions_body

        def boom(*a, **k):
            real(*a, **k)
            raise KeyboardInterrupt('simulated scheduler kill')

        mio._write_mpas_emissions_body = boom
        try:
            mio.write_mpas_emissions(out, times=times, n_cells=4, fields=fields)
        except KeyboardInterrupt:
            pass
        finally:
            mio._write_mpas_emissions_body = real
        assert not out.exists()
        assert not list(Path(td).glob('*.tmp.*'))


def test_prm_only_validation_rejects_legacy_variable_names():
    # The archived prebuilt PRM file carries area_biob_modis/_std. MPAS silently
    # drops stream variables it cannot find, so plume rise would run with fire
    # size and FRP identically zero; validation must make that fatal instead.
    import mpas_emissions.validate_streams as vs
    streams = """<streams>
<stream name="prm_lowbc_area_avg" type="input" filename_template="prm.nc" input_interval="none">
  <var name="firesize_biob_modis_avg"/>
</stream>
<stream name="anth_bc_emissions" type="input" filename_template="anth.nc" input_interval="none">
  <var name="bc_anth_sum"/>
</stream>
</streams>"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d/'streams.atmosphere').write_text(streams)
        (d/'prm.nc').write_bytes(b'CDF\x05' + b'\x00'*32)
        (d/'anth.nc').write_bytes(b'CDF\x05' + b'\x00'*32)
        old_vars = vs._variables
        try:
            # legacy PRM names present, new ones absent; emissions file is fine
            vs._variables = lambda p: ({'area_biob_modis', 'area_biob_modis_std'}
                                       if p.name == 'prm.nc' else {'bc_anth_sum'})
            try:
                vs.validate(d/'streams.atmosphere', d, only_prm=True)
            except RuntimeError as exc:
                assert 'firesize_biob_modis_avg' in str(exc)
                assert 'anth_bc_emissions' not in str(exc)
            else:
                raise AssertionError('legacy PRM variable names should fail validation')
        finally:
            vs._variables = old_vars


def test_finn_fallback_days_inherit_product_scaling():
    # missing_days.fallback holds {sources, force date ranges, scale method} and
    # no scaling key, so using it verbatim gave fallback days a factor of 1.0
    # while primary days were scaled -- a step change inside one output file.
    from mpas_emissions.scaling import scaling_factor
    cfg = {'scaling': {'species': {'NH3': 0.5}}}
    fallback_cfg = {'sources': [], 'force date ranges': [], 'scale method': 'none'}

    def resolve(is_primary):
        if is_primary:
            return cfg
        if isinstance(fallback_cfg, dict) and fallback_cfg.get('scaling'):
            return fallback_cfg
        return cfg

    primary = scaling_factor(resolve(True), species_aliases=['NH3'])
    fallback = scaling_factor(resolve(False), species_aliases=['NH3'])
    assert primary == 0.5
    assert fallback == primary, (primary, fallback)

    # an explicit fallback scaling block still wins
    fallback_cfg['scaling'] = {'species': {'NH3': 0.25}}
    assert scaling_factor(resolve(False), species_aliases=['NH3']) == 0.25


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print('review-regression tests passed')


if __name__ == '__main__':
    main()
