import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools.check_jedi_tables import check

ROOT = Path(__file__).resolve().parents[1]
class TablesTests(unittest.TestCase):
    def fixture(self, root):
        (root/'nml').write_text("config_radt_sw_scheme='rrtmg_sw'\nconfig_radt_lw_scheme='rrtmg_lw'\n")
        (root/'app.yaml').write_text('geometry:\n  nml_file: nml\n')
        for name in ('RRTMG_SW_DATA','RRTMG_LW_DATA'):(root/name).write_bytes(b'table')
        return root/'app.yaml'

    def test_valid_and_optional_files_not_required(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=self.fixture(root)
            self.assertEqual(len(check(config,root)),2)

    def test_missing_empty_and_dangling_tables_rejected(self):
        for mode in ('missing','empty','dangling'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root=Path(d);config=self.fixture(root);p=root/'RRTMG_SW_DATA';p.unlink()
                if mode=='empty':p.touch()
                if mode=='dangling':p.symlink_to(root/'absent')
                with self.assertRaisesRegex(ValueError,'RRTMG_SW_DATA'):check(config,root)

    def test_legacy_ensemble_pattern_and_nested_geometry(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=self.fixture(root)
            config.write_text('cost function:\n  geometry:\n    nml_file: nml\n  background:\n    pattern: %iMember%\n')
            self.assertEqual(len(check(config,root)),2)

    def test_non_rrtmg_does_not_require_tables(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=self.fixture(root)
            (root/'nml').write_text("! config_radt_sw_scheme='rrtmg_sw'\nconfig_radt_lw_scheme='off'\n")
            (root/'RRTMG_SW_DATA').unlink();(root/'RRTMG_LW_DATA').unlink()
            self.assertEqual(check(config,root),[])

    def test_all_launch_guards_stop_before_mpi(self):
        for app in ('Variational','EnKF','RecenterEnsemble','HofX'):
            with self.subTest(app=app),tempfile.TemporaryDirectory() as d:
                root=Path(d);self.fixture(root);(root/'RRTMG_SW_DATA').write_bytes(b'')
                text=(ROOT/'bin'/f'{app}.csh').read_text()
                guard=text[text.index('# Fail before MPI'):text.index('mpiexec ./${myEXE}')]
                # Use the test interpreter while retaining the real shell failure guard.
                guard=guard.replace('python "',f'"{sys.executable}" "',1)
                script=f'set pyDir = "{ROOT}/tools"\nset myYAML = app.yaml\nset appName = app\n'+guard+'touch mpi_started\n'
                result=subprocess.run(['tcsh','-f'],input=script,cwd=root,text=True,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertTrue((root/'FAIL').exists())
                self.assertFalse((root/'mpi_started').exists())

if __name__=='__main__':unittest.main()
