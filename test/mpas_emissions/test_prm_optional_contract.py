from pathlib import Path
import tempfile
from contextlib import redirect_stderr
from io import StringIO

import mpas_emissions.validate_streams as vs


def main():
    streams = '''<streams>
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
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d/'streams.atmosphere').write_text(streams)
        # Signature only; variable inspection is monkeypatched below.
        (d/'prm.nc').write_bytes(b'CDF\x05' + b'\x00'*32)
        old_vars = vs._variables
        try:
            vs._variables = lambda path: {'firesize_biob_modis_avg'}
            err = StringIO()
            with redirect_stderr(err):
                vs.validate(d/'streams.atmosphere', d)
            txt = err.getvalue()
            assert 'prm_lowbc_area_std' in txt
            assert 'prm_lowbc_frp_avg' in txt
            assert 'prm_lowbc_frp_std' in txt

            # Required AREA average remains fatal.
            vs._variables = lambda path: set()
            try:
                vs.validate(d/'streams.atmosphere', d)
            except RuntimeError as exc:
                assert 'prm_lowbc_area_avg' in str(exc)
            else:
                raise AssertionError('missing required PRM area average should fail')
        finally:
            vs._variables = old_vars
    print('PRM optional-contract validation test passed')


if __name__ == '__main__':
    main()
