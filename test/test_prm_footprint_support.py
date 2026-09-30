"""Geometry and end-to-end opt-in support package contracts."""
import hashlib
import sys
from pathlib import Path
import tempfile
import unittest
import numpy as np
from netCDF4 import Dataset
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prm_footprint_support import edges, pixel_ids, has_overlap, unique_records, generate


class Geometry(unittest.TestCase):
    def test_seam_and_positive_area(self):
        v=np.array([[-.2,179.8],[-.2,180.2],[.2,180.2],[.2,179.8]])
        self.assertTrue(has_overlap(0,180,v,[-180,-179.9,-.1,.1],1))
        self.assertFalse(has_overlap(0,180,v,[-179,-178.9,-.1,.1],1))
        # Shared meridian boundary, no positive area.
        self.assertFalse(has_overlap(0,180,v,[180.2,180.3,-.1,.1],1))
    def test_half_open_pixel_membership(self):
        r=np.array([[0,180,2,1],[0,-180,2,1],[90,0,2,1]])
        y,x=pixel_ids(r,[-90,0,90],[-180,0,180])
        np.testing.assert_array_equal(x,[0,0,1]);np.testing.assert_array_equal(y,[1,1,1])
    def test_bad_grid_and_duplicate(self):
        with self.assertRaises(ValueError): edges([0,1,3])
        with self.assertRaises(ValueError): unique_records(np.array([[0,0,2,1]]*2))


class Package(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.r=Path(self.tmp.name)
        self.mesh=self.r/'mesh.nc';self.fs=self.r/'fire.nc';self.veg=self.r/'veg.nc'
        self.native=self.r/'native.nc';self.target=self.r/'target.nc';self.raw=self.r/'finn.txt'
        self.manifest=self.r/'manifest.csv';self.out=self.r/'output'
        # Cells all overlap the source pixel: only zero-support emitting cell 0 changes.
        with Dataset(self.mesh,'w') as d:
            for k,n in [('nCells',3),('nVertices',4),('maxEdges',4)]:d.createDimension(k,n)
            for k in ['latCell','lonCell']:d.createVariable(k,'f8',('nCells',))[:]=0
            d.createVariable('latVertex','f8',('nVertices',))[:]=np.deg2rad([-.2,-.2,.2,.2])
            d.createVariable('lonVertex','f8',('nVertices',))[:]=np.deg2rad([-.2,.2,.2,-.2])
            d.createVariable('verticesOnCell','i4',('nCells','maxEdges'))[:]=[1,2,3,4]
            d.createVariable('nEdgesOnCell','i4',('nCells',))[:]=4
        fingerprint=hashlib.sha256(np.zeros((3,2),dtype='<f8').tobytes()).hexdigest()
        for path in [self.fs,self.veg,self.target]:
            with Dataset(path,'w') as d:
                for k,n in [('Time',1),('nCells',3),('StrLen',64)]:d.createDimension(k,n)
                d.createVariable('xtime','S1',('Time','StrLen'))[0]=np.frombuffer('2024-09-13_00:00:00'.ljust(64).encode(),dtype='S1')
                if path==self.fs:
                    d.createVariable('firesize_biob_modis_avg','f8',('Time','nCells'))[:]=[0,77,0]
                    d.createVariable('unrelated','f8',('Time','nCells'))[:]=[9,8,7]
                elif path==self.veg:
                    d.mesh_coordinates_sha256=fingerprint
                    for key in ['prm_source_vegetation','prm_source_record_count','prm_source_dominant_share','prm_source_known_area_fraction']:
                        d.createVariable(key,'f8',('Time','nCells'))[:]=[0,2,0]
                else:
                    for k in ['latCell','lonCell']:d.createVariable(k,'f8',('nCells',))[:]=0
                    d.createVariable('oc','f8',('Time','nCells'))[:]=[1,1,0]
        with Dataset(self.native,'w') as d:
            for k,n in [('time',1),('lat',180),('lon',360)]:d.createDimension(k,n)
            d.createVariable('lat','f8',('lat',))[:]=np.arange(-89.5,90)
            d.createVariable('lon','f8',('lon',))[:]=np.arange(-179.5,180)
            t=d.createVariable('time','f8',('time',));t.units='hours since 2024-09-13';t[:]=0
            f=d.createVariable('biomass','f8',('time','lat','lon'));f[:]=0;f[0,90,180]=1
        self.raw.write_text('DAY LATI LONGI GENVEG AREA\n257 0.1 0.1 2 100\n257 0.15 0.15 4 50\n')
        self.manifest.write_text(f'date,finn,native,variable,target,target_variable\n2024-09-13,{self.raw},{self.native},biomass,{self.target},oc\n')
    def run_it(self):return generate(self.mesh,self.manifest,self.fs,self.veg,self.out)
    def test_complete_package_preserves_inputs_and_supported_cells(self):
        before={p:p.read_bytes() for p in [self.fs,self.veg,self.target,self.native]}
        result=self.run_it()
        self.assertEqual(result['days'][0]['filled_cells'],1)
        with Dataset(self.out/'fire_size.nc') as d:
            np.testing.assert_array_equal(d['firesize_biob_modis_avg'][:],[[75,77,0]])
            np.testing.assert_array_equal(d['unrelated'][:],[[9,8,7]])
        with Dataset(self.out/'vegetation.nc') as d:
            np.testing.assert_array_equal(d['prm_source_vegetation'][:],[[3,2,0]])
            self.assertAlmostEqual(float(d['prm_source_dominant_share'][0,0]),2/3)
            self.assertEqual(d['prm_source_record_count'][0,0],2)
        for p,b in before.items():self.assertEqual(p.read_bytes(),b)
        with self.assertRaises(FileExistsError):self.run_it()
    def test_failure_leaves_no_package(self):
        with Dataset(self.native,'a') as d:d['time'][:]=24
        with self.assertRaisesRegex(ValueError,'date mismatch'):self.run_it()
        self.assertFalse(self.out.exists());self.assertFalse(list(self.r.glob('*.partial-*')))
    def test_no_detection_and_unknown_category_leave_missing(self):
        self.raw.write_text('DAY LATI LONGI GENVEG AREA\n257 0.1 0.1 0 100\n')
        self.assertEqual(self.run_it()['days'][0]['filled_cells'],0)
    def test_mesh_mismatch_and_scope_validation(self):
        with Dataset(self.veg,'a') as d:d.mesh_coordinates_sha256='wrong'
        with self.assertRaisesRegex(ValueError,'mesh mismatch'):self.run_it()
        self.assertFalse(self.out.exists())
        with self.assertRaisesRegex(ValueError,'cell indices'):
            generate(self.mesh,self.manifest,self.fs,self.veg,self.out,cells=[3])

    def test_empty_detection_file(self):
        self.raw.write_text('DAY LATI LONGI GENVEG AREA\n')
        result=self.run_it()
        self.assertEqual(result['days'][0]['filled_cells'],0)
        self.assertEqual(result['days'][0]['unfilled_cells'],1)

    def test_float32_coordinates_follow_existing_fingerprint_convention(self):
        # Existing generator converts radians to float64 before degrees.
        with Dataset(self.mesh,'a') as d:d['latCell'][:]=np.float32(0.001)
        with Dataset(self.target,'a') as d:d['latCell'][:]=np.float32(0.001)
        coords=np.column_stack([np.rad2deg(np.full(3,float(np.float32(0.001)))),np.zeros(3)])
        with Dataset(self.veg,'a') as d:d.mesh_coordinates_sha256=hashlib.sha256(coords.astype('<f8').tobytes()).hexdigest()
        self.assertEqual(self.run_it()['days'][0]['filled_cells'],1)

    def test_legacy_scrip_verifies_corners(self):
        grid=self.r/'scrip.nc'
        with Dataset(self.target,'a') as d:d.mesh_fingerprint='legacy'
        with Dataset(grid,'w') as d, Dataset(self.mesh) as mesh:
            d.mesh_fingerprint='legacy'
            d.createDimension('grid_size',3);d.createDimension('grid_corners',4)
            for kind in ['lat','lon']:
                v=d.createVariable('grid_center_'+kind,'f8',('grid_size',));v.units='radians';v[:]=mesh[kind+'Cell'][:]
                v=d.createVariable('grid_corner_'+kind,'f8',('grid_size','grid_corners'));v.units='radians';v[:]=mesh[kind+'Vertex'][:]
        lines=self.manifest.read_text().splitlines()
        self.manifest.write_text(lines[0]+',target_scrip\n'+lines[1]+','+str(grid)+'\n')
        self.assertEqual(self.run_it()['days'][0]['filled_cells'],1)
        self.out=self.r/'bad'
        with Dataset(grid,'a') as d:d['grid_corner_lat'][0,0]+=0.01
        with self.assertRaisesRegex(ValueError,'vertex geometry mismatch'):self.run_it()
        self.assertFalse(self.out.exists())

    def test_reproducible(self):
        a=self.run_it();self.out=self.r/'other';b=self.run_it();self.assertEqual(a,b)


if __name__=='__main__':unittest.main()
