"""Small end-to-end NetCDF fixtures for the production input generator."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, chartostring

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prm_source_vegetation import generate


class Generator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mesh = self.root / 'mesh.nc'
        self.ref = self.root / 'fire.nc'
        self.manifest = self.root / 'manifest.csv'
        self.output = self.root / 'vegetation.nc'
        with Dataset(self.mesh, 'w') as d:
            d.createDimension('nCells', 2)
            d.createVariable('latCell', 'f8', ('nCells',))[:] = [0, 0]
            d.createVariable('lonCell', 'f8', ('nCells',))[:] = [0, np.pi / 2]
        raw = self.root / 'fires.txt'
        raw.write_text('DAY,GENVEG,LATI,LONGI,AREA\n254,2,0,0,10\n254,3,0,90,20\n')
        self.manifest.write_text(f'date,path\n2024-09-10,{raw}\n')

    def reference(self, stamps, means=(10, 20)):
        with Dataset(self.ref, 'w') as d:
            d.createDimension('Time', len(stamps))
            d.createDimension('StrLen', 64)
            d.createDimension('nCells', 2)
            xt = d.createVariable('xtime', 'S1', ('Time', 'StrLen'))
            for i, stamp in enumerate(stamps):
                xt[i] = np.frombuffer(stamp.ljust(64).encode(), dtype='S1')
            d.createVariable('firesize_biob_modis_avg', 'f8', ('Time', 'nCells'))[:] = means

    def run_generator(self):
        return generate(self.mesh, self.manifest, self.ref, self.output)

    def test_timestamps_layout_and_provenance(self):
        for hour in ('00', '12'):
            with self.subTest(hour=hour):
                stamp = f'2024-09-10_{hour}:00:00'
                self.reference([stamp])
                result = self.run_generator()
                with Dataset(self.output) as d:
                    self.assertTrue(d.dimensions['Time'].isunlimited())
                    self.assertEqual(len(d.dimensions['nCells']), 2)
                    self.assertEqual(len(d.dimensions['StrLen']), 64)
                    self.assertEqual(str(chartostring(d['xtime'][:])[0]).strip(), stamp)
                    self.assertEqual(d['prm_source_vegetation'].dimensions, ('Time', 'nCells'))
                    np.testing.assert_array_equal(d['prm_source_vegetation'][:], [[3, 1]])
                    np.testing.assert_array_equal(d['prm_source_record_count'][:], [[1, 1]])
                    np.testing.assert_allclose(d['prm_source_known_area_fraction'][:], 1)
                metadata = json.loads(self.output.with_suffix('.nc.json').read_text())
                self.assertEqual(metadata['sources'], result)
                self.assertNotIn('airport_category', result[0])
                self.assertEqual(len(result[0]['sha256']), 64)
                self.output.unlink()

    def test_inconsistent_mean_cleans_partial(self):
        self.reference(['2024-09-10_00:00:00'], (100, 20))
        with self.assertRaisesRegex(ValueError, 'do not reproduce'):
            self.run_generator()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.name + '.partial').exists())
        self.assertFalse(self.output.with_suffix('.nc.json').exists())

    def test_missing_or_duplicate_reference_date(self):
        for stamps in (['2024-09-11_00:00:00'], ['2024-09-10_00:00:00', '2024-09-10_12:00:00']):
            with self.subTest(stamps=stamps):
                self.reference(stamps)
                with self.assertRaisesRegex(ValueError, 'one matching daily'):
                    self.run_generator()
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_name(self.output.name + '.partial').exists())

    def test_existing_output_untouched(self):
        self.output.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            self.run_generator()
        self.assertEqual(self.output.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
