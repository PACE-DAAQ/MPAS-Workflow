from pathlib import Path
import tempfile
import numpy as np
import yaml
from scipy.io import netcdf_file

from mpas_emissions.cams_regrid import CamsProcessor
from mpas_emissions.mesh import MpasMesh
from mpas_emissions.sparse_weights import SparseWeights


def write_source(path: Path):
    with netcdf_file(path, 'w', version=2) as ds:
        ds.createDimension('time',12); ds.createDimension('lat',2); ds.createDimension('lon',2)
        t=ds.createVariable('time','f',('time',)); t[:] = np.arange(12,dtype=np.float32)
        lat=ds.createVariable('lat','f',('lat',)); lat[:] = [-45.,45.]
        lon=ds.createVariable('lon','f',('lon',)); lon[:] = [-90.,90.]
        awb=ds.createVariable('awb','f',('time','lat','lon'))
        sm=ds.createVariable('sum','f',('time','lat','lon'))
        base=np.array([[10.,20.],[30.,40.]],dtype=np.float32)
        fire=np.array([[1.,2.],[3.,4.]],dtype=np.float32)
        for i in range(12):
            sm[i]=base+i; awb[i]=fire


def write_weights(path: Path):
    # dst0 = mean of southern row; dst1 = mean of northern row.
    with netcdf_file(path,'w',version=2) as ds:
        ds.createDimension('n_s',4)
        S=ds.createVariable('S','d',('n_s',)); S[:] = [0.5,0.5,0.5,0.5]
        # Deliberately use two row blocks, matching the production ESMF file's
        # non-global row ordering.
        row=ds.createVariable('row','i',('n_s',)); row[:] = [1,2,1,2]
        col=ds.createVariable('col','i',('n_s',)); col[:] = [1,3,2,4]


def write_mesh(path: Path):
    R=6371229.0
    with netcdf_file(path,'w',version=2) as ds:
        ds.createDimension('nCells',2); ds.createDimension('maxEdges',4); ds.createDimension('nVertices',6)
        lat=ds.createVariable('latCell','d',('nCells',)); lat[:] = np.deg2rad([-45.,45.])
        lon=ds.createVariable('lonCell','d',('nCells',)); lon[:] = [0.,0.]
        area=ds.createVariable('areaCell','d',('nCells',)); area[:] = [2*np.pi*R*R,2*np.pi*R*R]
        ned=ds.createVariable('nEdgesOnCell','i',('nCells',)); ned[:] = [4,4]
        coc=ds.createVariable('cellsOnCell','i',('nCells','maxEdges')); coc[:] = [[2,0,0,0],[1,0,0,0]]
        bdy=ds.createVariable('bdyMaskCell','i',('nCells',)); bdy[:] = [0,0]
        voc=ds.createVariable('verticesOnCell','i',('nCells','maxEdges')); voc[:] = [[1,2,3,4],[3,4,5,6]]
        lv=ds.createVariable('latVertex','d',('nVertices',)); lv[:] = np.deg2rad([-90,-90,0,0,90,90])
        lov=ds.createVariable('lonVertex','d',('nVertices',)); lov[:] = np.deg2rad([-180,0,0,180,180,0])
        ds.sphere_radius = R


def main():
    with tempfile.TemporaryDirectory() as td:
        d=Path(td); src=d/'CAMS_black-carbon_2024.nc'; meshf=d/'mesh.nc'; wf=d/'w.nc'
        write_source(src); write_mesh(meshf); write_weights(wf)
        cfg=d/'cams.yaml'
        cfg.write_text(yaml.safe_dump({
            'year':2024,
            'species':[{'black-carbon':'bc'}],
            'inp_file_format':str(d/'CAMS_SPC_2024.nc'),
            'sectors':[],
            'sector_exclude':['awb'],
        }))
        mesh=MpasMesh.open(meshf)
        inside=mesh.points_inside_cells(np.array([0,1]),np.array([-45.,45.]),np.array([0.,0.]))
        assert inside.tolist() == [True, True]
        proc=CamsProcessor(mesh,d/'cache',d/'out',provided_weight_file=wf,chunk_links=2)
        outs=proc.process_config(cfg,kind='anth',reuse_existing=False)
        assert len(outs)==1
        with netcdf_file(outs[0],'r',mmap=False) as ds:
            got=np.asarray(ds.variables['bc_anth_sum'].data).copy()
            fire=np.asarray(ds.variables['bc_anth_awb'].data).copy()
        np.testing.assert_allclose(got[0],[13.5,31.5],rtol=0,atol=1e-6)
        np.testing.assert_allclose(fire,0.0,rtol=0,atol=0)
        w=SparseWeights.open(wf,n_dest=2,n_src=4,require_full_source=True)
        np.testing.assert_allclose(w.apply([10,20,30,40],chunk_links=2),[15,35])
        print('end-to-end CAMS sparse test passed')

if __name__=='__main__': main()
