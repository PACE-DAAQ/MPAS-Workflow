from pathlib import Path
import tempfile

import mpas_emissions.validate_streams as vs


STREAMS = '''<streams>
<stream name="prm_lowbc_area_avg" type="input" filename_template="prm.nc" input_interval="none">
  <var name="firesize_biob_modis_avg"/>
</stream>
<stream name="prm_lowbc_area_std" type="input" filename_template="prm.nc" input_interval="none">
  <var name="firesize_biob_modis_std"/>
</stream>
<stream name="prm_lowbc_frp_avg" type="input" filename_template="prm.nc" input_interval="none">
  <var name="frp_biob_modis_avg"/>
</stream>
<stream name="prm_lowbc_frp_std" type="input" filename_template="prm.nc" input_interval="none">
  <var name="frp_biob_modis_std"/>
</stream>
</streams>'''

ALL_PRM_VARS = {
    'firesize_biob_modis_avg',
    'firesize_biob_modis_std',
    'frp_biob_modis_avg',
    'frp_biob_modis_std',
}


def main():
    # gocartMPAS unconditionally reads all four lowbc streams, so every staged
    # PRM file must carry AREA mean/std and FRP mean/std.  Preprocessing may
    # zero-fill fields the upstream source does not provide.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d/'streams.atmosphere').write_text(STREAMS)
        # Signature only; variable inspection is monkeypatched below.
        (d/'prm.nc').write_bytes(b'CDF\x05' + b'\x00'*32)
        old_vars = vs._variables
        try:
            # A complete staged file validates.
            vs._variables = lambda path: set(ALL_PRM_VARS)
            items = vs.validate(d/'streams.atmosphere', d)
            assert len(items) == 4

            # Each of the four fields is individually fatal when absent.
            for stream, missing in (
                ('prm_lowbc_area_avg', 'firesize_biob_modis_avg'),
                ('prm_lowbc_area_std', 'firesize_biob_modis_std'),
                ('prm_lowbc_frp_avg', 'frp_biob_modis_avg'),
                ('prm_lowbc_frp_std', 'frp_biob_modis_std'),
            ):
                vs._variables = lambda path, m=missing: ALL_PRM_VARS - {m}
                try:
                    vs.validate(d/'streams.atmosphere', d)
                except RuntimeError as exc:
                    assert stream in str(exc)
                    assert missing in str(exc)
                else:
                    raise AssertionError(
                        'missing PRM field %s should fail validation' % missing)
        finally:
            vs._variables = old_vars
    print('PRM required-contract validation test passed')


if __name__ == '__main__':
    main()
