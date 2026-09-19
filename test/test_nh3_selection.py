"""Exercise the real csh helper against both model stream templates."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('csh'), 'csh required')
class NH3Selection(unittest.TestCase):
    def run_case(self, template, settings='', missing=False):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            shutil.copy(ROOT / template, work / 'streams')
            script = ('set nCells = 163842\nset meshRatio = 1\n'
                      'set thisCycleDate = 2024081500\nset StreamsFile = streams\n'
                      'set memberVariants = ()\nset ArgMember = 0\n' + settings + '\n')
            if missing:
                script += f'set PhysicsSuite = MPAS-GOCART2G\nset EmissionDir = {td}\n'
            script += f'source {ROOT}/bin/SetStreamsVariant.csh\n'
            result = subprocess.run(['csh', '-f'], input=script, text=True,
                                    cwd=td, capture_output=True)
            tree = ET.parse(work / 'streams')
            filename = tree.find(".//stream[@name='anth_nh3_emissions']").get('filename_template')
            failure = (work / 'FAIL').read_text() if (work / 'FAIL').exists() else ''
            return result, filename, failure

    def test_routing(self):
        cases = [
            ('set streamsVariant = pert03', 'CAMS-anth'),
            ('set streamsVariant = pert03\nset anthNh3Emissions = ""', 'CAMS-anth'),
            ('set streamsVariant = pert03\nset anthNh3Emissions = follow-anth', 'CEDS'),
            ('set anthNh3Emissions = ceds', 'CEDS'),
            ('set streamsVariant = pert03\nset anthNh3Emissions = cams', 'CAMS-anth'),
            ('set streamsVariant = pert06\nset anthNh3Emissions = follow-anth', 'CAMS-anth'),
            ('set anthEmissions = ceds\nset anthNh3Emissions = follow-anth', 'CEDS'),
            ('set streamsVariant = pert03\nset anthEmissions = cams\nset anthNh3Emissions = follow-anth', 'CAMS-anth'),
            ('set memberVariants = (cntl pert04)\nset ArgMember = 2\nset anthNh3Emissions = follow-anth', 'CEDS'),
        ]
        for template in ('config/mpas/forecast/streams.atmosphere',
                         'config/mpas/initic/streams.init_atmosphere.gocart2g'):
            for settings, inventory in cases:
                with self.subTest(template=template, settings=settings):
                    result, filename, failure = self.run_case(template, settings)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertFalse(failure)
                    self.assertEqual(filename, f'{inventory}_x1.163842_2024_nh3_monthly.nc')
                    if 'pert06' in settings:
                        self.assertIn('fallback', result.stdout)

    def test_invalid_selection(self):
        result, _, failure = self.run_case('config/mpas/forecast/streams.atmosphere',
                                           'set anthNh3Emissions = typo')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unknown anth nh3 emissions', failure)

    def test_missing_ceds_does_not_fallback(self):
        result, filename, failure = self.run_case('config/mpas/forecast/streams.atmosphere',
                                                 'set anthNh3Emissions = ceds', True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(filename, 'CEDS_x1.163842_2024_nh3_monthly.nc')
        self.assertIn(filename, failure)

if __name__ == '__main__':
    unittest.main()
