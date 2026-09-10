"""FINNv1 (MOZ4) reader regressions.

FINNv1 is the only FINN generation covering the 2024-09-09..09-15 and
2024-09-26..09-30 windows that FINNv2.5 NRT does not publish, so it is the sole
source of plume-rise fire size over the September 2024 PACE-PAX fire period.
Its text format differs from FINNv2 in two ways that both used to fail silently:

  * every field is written in Fortran ``D`` exponent form (``0.7500000000D+06``),
    which ``pd.to_numeric`` maps to NaN; ``_aggregate_prm_stats`` then masks the
    NaNs out of its bincount and emits an all-zero fire-size field with exit 0;
  * data rows carry a trailing comma, so pandas silently promotes column 0 to
    the index and shifts every name one place left (LATI reads GENVEG,
    longitudes come back as 1e6).

Both must fail loudly rather than produce zero PRM.
"""

import textwrap

import numpy as np
import pandas as pd

from mpas_emissions.finn_cli import (
    _clean_numeric,
    _date_from_name,
    _find_column,
    _require_parsed,
    _to_numeric_fortran,
)


# Two FINNv1 MOZ4 rows, verbatim in shape: Fortran D exponents and the
# trailing comma that makes each data line one field longer than the header.
V1_TEXT = textwrap.dedent(
    """\
    DAY,TIME,GENVEG,LATI,LONGI,AREA,CO2,CO,OC,BC
       257,  1236,     1,   -0.5640639782D+01,   -0.4283892059D+02,    0.7500000000D+06,    0.1559823300D+08,    0.8546046875D+06,    0.1501163940D+03,    0.2892783447D+04,
       257,   739,     2,    0.3428000000D+02,   -0.1177500000D+03,    0.1000000000D+07,    0.2715615600D+08,    0.1690823000D+07,    0.2000000000D+03,    0.3000000000D+04,
    """
)


def _write(tmpdir, name, text):
    p = tmpdir / name
    p.write_text(text)
    return p


def test_fortran_d_exponent_parses():
    s = pd.Series(["0.7500000000D+06", "1.559E+07", "-0.4283892059D+01", "", "nan"])
    values, unparsed = _to_numeric_fortran(s)
    assert unparsed == 0
    got = values.to_numpy(float)
    assert np.isclose(got[0], 750000.0)
    assert np.isclose(got[1], 15590000.0)
    assert np.isclose(got[2], -4.283892059)
    assert np.isnan(got[3]) and np.isnan(got[4])


def test_plain_floats_are_untouched():
    """FINNv2 values parse on the first pass, so the D retry must not run."""
    s = pd.Series(["35.686", "43.878", "7.734E+05"])
    values, unparsed = _to_numeric_fortran(s)
    assert unparsed == 0
    assert np.allclose(values.to_numpy(float), [35.686, 43.878, 773400.0])


def test_lat_lon_are_cleaned_too():
    """_clean_numeric used to skip LATI/LONGI, which is where FINNv1 breaks."""
    df = pd.DataFrame({"LATI": ["-0.5640639782D+01"], "LONGI": ["-0.4283892059D+02"],
                       "AREA": ["0.7500000000D+06"]})
    out = _clean_numeric(df)
    assert np.isclose(out["LATI"].iloc[0], -5.640639782)
    assert np.isclose(out["LONGI"].iloc[0], -42.83892059)
    assert np.isclose(out["AREA"].iloc[0], 750000.0)


def test_trailing_comma_does_not_shift_columns(tmp_path=None):
    import tempfile
    from pathlib import Path

    from mpas_emissions.finn_cli import _read_finn_csv

    with tempfile.TemporaryDirectory() as d:
        p = _write(Path(d), "GLOB_MOZ4_2024257.txt", V1_TEXT)
        df = _read_finn_csv(str(p))
        cleaned = _clean_numeric(df)
        lat = cleaned[_find_column(cleaned.columns, ("LATI", "LAT"))].to_numpy(float)
        lon = cleaned[_find_column(cleaned.columns, ("LONGI", "LON"))].to_numpy(float)
        # Without index_col=False these come back as GENVEG/LATI values.
        assert np.isclose(lat[0], -5.640639782), lat
        assert np.isclose(lon[0], -42.83892059), lon
        assert np.all(np.abs(lat) <= 90.0)
        assert np.all(np.abs(lon) <= 180.0)


def test_v1_doy_filename_dates():
    assert _date_from_name("GLOB_MOZ4_2024257.txt.gz").strftime("%Y-%m-%d") == "2024-09-13"
    assert _date_from_name("GLOB_MOZ4_2024001.txt.gz").strftime("%Y-%m-%d") == "2024-01-01"
    assert _date_from_name("GLOB_MOZ4_2024366.txt.gz").strftime("%Y-%m-%d") == "2024-12-31"
    # FINNv2 YYYYMMDD must keep winning over the DOY branch.
    assert _date_from_name(
        "FINNv2.5.1_modvrs_nrt_MOZART_20241025.txt"
    ).strftime("%Y-%m-%d") == "2024-10-25"


def test_unparsed_column_is_fatal():
    """An all-NaN mandatory column must raise, never aggregate to zero."""
    try:
        _require_parsed("AREA", np.full(1000, np.nan), 1000, source_label="GLOB_MOZ4_2024257.txt")
    except ValueError as exc:
        assert "zero usable values" in str(exc)
        assert "GLOB_MOZ4_2024257.txt" in str(exc)
    else:
        raise AssertionError("all-NaN PRM column must be rejected")


def test_partially_unparsed_column_is_fatal():
    values = np.arange(1000.0)
    values[:50] = np.nan
    try:
        _require_parsed("AREA", values, 50)
    except ValueError as exc:
        assert "unparsed" in str(exc)
    else:
        raise AssertionError("5% unparsed must exceed the 2% tolerance")


def test_small_gaps_are_tolerated():
    """Genuinely absent values in a handful of rows must not block a run."""
    values = np.arange(1000.0)
    values[:10] = np.nan
    _require_parsed("AREA", values, 10)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("FINNv1 reader tests passed")


if __name__ == "__main__":
    main()
