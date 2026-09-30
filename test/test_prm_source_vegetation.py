import sys,unittest,tempfile
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from prm_source_vegetation import aggregate,read_records
class SourceVegetation(unittest.TestCase):
 def test_crosswalk(self):
  c,*_=aggregate(np.arange(8),[0,1,2,3,4,5,6,9],np.ones(8),8)
  np.testing.assert_array_equal(c,[0,4,3,1,2,2,2,4])
 def test_area_not_record_count_and_merge_classes(self):
  c,s,k,n,m=aggregate([0,0,0,1,1],[2,2,3,4,5],[1,1,8,3,4],3)
  np.testing.assert_array_equal(c,[1,2,0]);np.testing.assert_allclose(s,[.8,1,0]);np.testing.assert_allclose(m,[10/3,3.5,0])
 def test_unknown_and_zero_area(self):
  c,s,k,*_=aggregate([0,0,1],[0,2,2],[9,1,0],2)
  np.testing.assert_array_equal(c,[3,0]);np.testing.assert_allclose(k,[.1,0])
 def test_tie_and_bad_inputs(self):
  self.assertEqual(aggregate([0,0],[3,2],[2,2],1)[0][0],1)
  for g,a in [([7],[1]),([2],[-1]),([2],[np.nan])]:
   with self.assertRaises(ValueError):aggregate([0],g,a,1)
 def test_v1_trailing_comma_and_fortran_exponent(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.txt';p.write_text('DAY,TIME,GENVEG,LATI,LONGI,AREA\n254,1206,2,33.65D0,-117.5D0,1D6,\n')
   r=read_records(p,date(2024,9,10));np.testing.assert_allclose(r,[[33.65,-117.5,2,1e6]])
   with self.assertRaises(ValueError):read_records(p,date(2024,9,11))
 def test_v2_whitespace_and_missing_header(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x';p.write_text('DAY,GENVEG,LATI,LONGI,AREA\n254 2 33.65 -117.5 1e6\n')
   self.assertEqual(read_records(p,date(2024,9,10)).shape,(1,4))
 def test_negative_area_excluded_and_recorded(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x';p.write_text('DAY,GENVEG,LATI,LONGI,AREA\n254,2,33.65,-117.5,-999\n254,2,33.65,-117.5,1e6\n')
   rejected={};r=read_records(p,date(2024,9,10),rejected)
   self.assertEqual(r.shape,(1,4));self.assertEqual(rejected,{'negative_area':1})
if __name__=='__main__':unittest.main()
