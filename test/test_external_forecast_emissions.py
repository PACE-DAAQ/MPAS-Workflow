"""Render actual suite graphs without running jobs or touching experiment directories."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from initialize.config.Config import Config
from initialize.suites.SuiteBase import SuiteLookup

ROOT = Path(__file__).resolve().parents[1]

class ExternalForecastEmissionsTests(unittest.TestCase):
    def render(self, mode, chemistry='prebuilt', ensemble=False):
        cfg = Config(str(ROOT/'scenarios/3dhybrid_OIE60km_WStart.ensrec.yaml'))
        t = cfg._table
        t['experiment'] = {'name': 'external_emissions_test'}
        t['hpc'].update({'top directory': '/tmp/external-emissions-tests',
                         'TMPDIR': '/tmp/external-emissions-tests/tmp'})
        t['externalanalyses'] = {'resource': 'GEFSMERRA.PANDAC' if ensemble else 'GFS.RDA'}
        t['members'] = {'n': 2 if ensemble else 1}
        t['extendedforecast'] = {'meanTimes': None if ensemble else 'T00',
                                'ensTimes': 'T00' if ensemble else None,
                                'lengthHR': 72, 'post': [], 'updateSea': False}
        t['initic'] = {'chemistry mode': chemistry}
        t['emissions'] = {'mode': mode, 'prepare': ['mesh', 'cams', 'gfas', 'finn'],
                          'seed prebuilt': False}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'scenarios').symlink_to(ROOT/'scenarios', target_is_directory=True)
            (work/'config/auto').mkdir(parents=True)
            for p in (ROOT/'config').iterdir():
                if p.name != 'auto':
                    (work/'config'/p.name).symlink_to(p, target_is_directory=p.is_dir())
            previous = os.getcwd()
            try:
                os.chdir(work)
                with patch.dict(os.environ, {'NCAR_HOST':'derecho', 'USER':'test', 'CYLC_ENV':'/unused'}), patch('subprocess.run'):
                    suite = SuiteLookup('ForecastFromExternalAnalyses', cfg)
                    suite.submit()
                flow = (work/'flow.cylc').read_text()
                auto = {p.name:p.read_text() for p in (work/'config/auto').glob('*.csh')}
                return suite, flow, auto
            finally:
                os.chdir(previous)

    def test_workflow_prepares_emissions_before_forecasts(self):
        for chemistry in ('off', 'prebuilt', 'workflow'):
            for ensemble in (False, True):
                with self.subTest(chemistry=chemistry, ensemble=ensemble):
                    suite, flow, auto = self.render('workflow', chemistry, ensemble)
                    self.assertIn('[[PrepareEmissions]]', flow)
                    self.assertIn('PrepareEmissions => PreExtendedForecast__', flow)
                    self.assertEqual(suite.c['initic']['initicEmissionMode'], 'workflow')
                    self.assertEqual(suite.c['initic']['initicEmissionWorkDir'], 'Emissions/60km')
                    self.assertIn('setenv emissionsMode "workflow"', auto['emissions.csh'])
                    self.assertIn('setenv emissionsPrepareGfas "True"', auto['emissions.csh'])
                    if chemistry != 'off' and not ensemble:
                        self.assertIn('PrepareEmissions => ExternalAnalysisToMPAS', flow)

    def test_prebuilt_has_no_preparation_task_or_dependency(self):
        for chemistry in ('off', 'prebuilt'):
            suite, flow, auto = self.render('prebuilt', chemistry)
            self.assertNotIn('[[PrepareEmissions]]', flow)
            self.assertNotIn('PrepareEmissions =>', flow)
            self.assertEqual(suite.c['initic']['initicEmissionMode'], 'prebuilt')
            self.assertIn('setenv emissionsMode "prebuilt"', auto['emissions.csh'])

if __name__ == '__main__':
    unittest.main()
