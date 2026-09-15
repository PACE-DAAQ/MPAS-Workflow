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
TABLE = Path('/glade/derecho/scratch/syha/pace_cycling_2024/runs/matched_dust_cycling/member_design_12_candidate.csv')

class Routing(unittest.TestCase):
    def test_count_and_invalid(self):
        self.assertEqual(len(read_members(TABLE, 12)), 12)
        with self.assertRaises(ValueError): read_members(TABLE, 9)
        for value in [-1, 'nan', 'inf']:
            with self.assertRaises(ValueError): factor(value)

    def test_central_and_all_members(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for role, member in [('central', 1)] + [('ensemble', m) for m in range(1, 13)]:
                cfg = d/'member.csh'
                nl = d/'namelist.atmosphere'
                nl.write_text('&chemistry\n/\n')
                subprocess.run([sys.executable, str(ROOT/'tools/emission_members.py'), '--table',str(TABLE),
                    '--count','12','--member',str(member),'--role',role,'--dust','0.4','--seasalt','1',
                    '--csh',str(cfg),'--namelist',str(nl)],check=True)
                out = json.loads(Path(str(cfg)+'.json').read_text())
                if role == 'central':
                    self.assertIsNone(out['inventories'])
                    self.assertEqual(out['dust_factor'], .4)
                    self.assertNotIn('biobEmissions', cfg.read_text())
                else:
                    self.assertEqual(out['inventories']['biob'], ['finn','gfas','qfed','gbbepx'][(member-1)%4])
                self.assertEqual(nl.read_text().count('config_gocart2G_dust_emission_factor'), 1)

if __name__ == '__main__': unittest.main()
