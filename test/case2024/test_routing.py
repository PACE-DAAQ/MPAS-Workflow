import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.emission_members import read_members, factor

ROOT = Path(__file__).resolve().parents[2]
BIOB = ['finn', 'gfas', 'qfed', 'gbbepx']


def write_member_table(path, count=12):
    """Write a self-contained 12-member design table (same columns as the
    matched_dust_cycling candidate table) so the test needs no external file."""
    with open(path, 'w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['member', 'anth', 'biob', 'biog', 'dust_relative', 'seasalt_relative'])
        for member in range(1, count + 1):
            writer.writerow([member, 'cams', BIOB[(member - 1) % len(BIOB)], 'cams',
                             1.0 + 0.1 * ((member - 1) % 3), 1.0 + 0.3 * ((member - 1) % 2)])
    return path


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.table = write_member_table(Path(cls._tmp.name) / 'member_design_12.csv')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_count_and_invalid(self):
        self.assertEqual(len(read_members(self.table, 12)), 12)
        with self.assertRaises(ValueError): read_members(self.table, 9)
        for value in [-1, 'nan', 'inf']:
            with self.assertRaises(ValueError): factor(value)

    def test_central_and_all_members(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for role, member in [('central', 1)] + [('ensemble', m) for m in range(1, 13)]:
                cfg = d/'member.csh'
                nl = d/'namelist.atmosphere'
                nl.write_text('&chemistry\n/\n')
                subprocess.run([sys.executable, str(ROOT/'tools/emission_members.py'), '--table',str(self.table),
                    '--count','12','--member',str(member),'--role',role,'--dust','0.4','--seasalt','1',
                    '--csh',str(cfg),'--namelist',str(nl)],check=True)
                out = json.loads(Path(str(cfg)+'.json').read_text())
                if role == 'central':
                    self.assertIsNone(out['inventories'])
                    self.assertEqual(out['dust_factor'], .4)
                    self.assertNotIn('biobEmissions', cfg.read_text())
                else:
                    self.assertEqual(out['inventories']['biob'], BIOB[(member-1)%4])
                self.assertEqual(nl.read_text().count('config_gocart2G_dust_emission_factor'), 1)

if __name__ == '__main__': unittest.main()
