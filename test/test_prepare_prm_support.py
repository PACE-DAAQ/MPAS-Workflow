"""Exercise YAML -> automatic manifest -> paired inputs -> forecast configuration."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
import numpy as np
from netCDF4 import Dataset
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_prm_support import prepare
from configure_prm_source_vegetation import configure
import test_prm_footprint_support as fixtures


class Preparation(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.Package();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.f=fixture
        for path in [fixture.mesh,fixture.target]:
            with Dataset(path,'a') as d:d['lonCell'][:]=np.deg2rad([-.6,.6,2])
        with Dataset(fixture.fs,'a') as d:d['firesize_biob_modis_avg'][:]=[0,75,0]
        self.cfg=fixture.r/'support.yaml'
        self.settings={'method':'finn_point','native root':str(fixture.r),'finn':{'pattern':'finn.txt'}}
        self.out=fixture.r/'prepared'
    def run_it(self):
        self.cfg.write_text(yaml.safe_dump(self.settings))
        prepare(self.cfg,self.f.mesh,self.f.fs,self.out)
    def test_point_and_reuse_then_stale_rejection(self):
        self.run_it();self.run_it()
        self.assertIn('2024-09-13', (self.out/'finn_manifest.csv').read_text())
        with Dataset(self.out/'vegetation.nc') as d:np.testing.assert_array_equal(d['prm_source_vegetation'][:],[[0,3,0]])
        self.f.raw.write_text(self.f.raw.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'stale'):self.run_it()
    def test_overlap_and_forecast_staging(self):
        self.settings['method']='footprint_overlap'
        self.settings['footprint']={'native pattern':'native.nc','variable':'biomass','target':'target.nc','target variable':'oc','cells':[0]}
        self.run_it()
        with Dataset(self.out/'fire_size.nc') as d:np.testing.assert_array_equal(d['firesize_biob_modis_avg'][:],[[75,75,0]])
        n=self.f.r/'namelist';s=self.f.r/'streams'
        n.write_text('&plumerisemodel\n config_prm_lowbc_interval = 01_00:00:00\n/\n')
        s.write_text('<streams/>')
        configure(self.out/'vegetation.nc',self.out/'fire_size.nc',n,s,3,self.f.mesh,'2024-09-13_00:00:00',6)
        self.assertIn('config_prm_source_vegetation = true',n.read_text())
        self.assertIn(str(self.out/'vegetation.nc'),s.read_text())
    def test_explicit_override_and_missing_day(self):
        self.settings['finn']={'pattern':'absent_%Y%m%d.txt','overrides':{'2024-09-13':'finn.txt'}}
        self.run_it()
        self.settings['finn']['overrides']={};self.out=self.f.r/'missing'
        with self.assertRaises(FileNotFoundError):self.run_it()
        self.assertFalse(self.out.exists())
    def test_wrong_source_rejected_by_fire_size_consistency(self):
        self.f.raw.write_text(self.f.raw.read_text().replace('100','200'))
        with self.assertRaisesRegex(ValueError,'do not reproduce'):self.run_it()
        self.assertFalse(self.out.exists())
    def test_actual_shell_preparation_and_forecast(self):
        import os,subprocess
        self.cfg.write_text(yaml.safe_dump(self.settings))
        repo=Path(__file__).resolve().parents[1]
        work=self.f.r/'work';work.mkdir()
        (work/'fire.nc').symlink_to(self.f.fs)
        def shell(block,variables,cwd):
            prefix=''.join(f'set {k} = "{v}"\n' for k,v in variables.items())
            env=dict(os.environ,PATH=str(Path(sys.executable).parent)+':'+os.environ['PATH'])
            return subprocess.run(['tcsh','-f'],input=prefix+block,text=True,cwd=cwd,env=env,check=True,capture_output=True)
        script=(repo/'bin/PrepareEmissions.csh').read_text()
        block=script[script.index('# Generate separately'):script.index('echo "PrepareEmissions complete')]
        shell(block,dict(emissionsPrmVegetation='True',emissionsPrmVegetationConfig=self.cfg,
              emissionsPrmVegetationFireSize='',PRMAreaFile='fire.nc',emissionYear=2024,
              nCellsOuter=3,emissionsGridName='test',outDir=work,toolsDir=repo/'tools',
              meshFile=self.f.mesh,py=sys.executable),work)
        self.assertTrue((work/'prm_support/validated.json').exists())
        fc=self.f.r/'forecast';fc.mkdir()
        (fc/'fire.nc').symlink_to(self.f.fs)
        (self.f.r/'tools').symlink_to(repo/'tools');(self.f.r/'config').mkdir()
        (self.f.r/'config/environmentEmissions.csh').write_text('# active environment\n')
        n=fc/'namelist';n.write_text('&plumerisemodel\n config_prm_lowbc_interval = 01_00:00:00\n/\n')
        st=fc/'streams';st.write_text('<streams/>')
        script=(repo/'bin/Forecast.csh').read_text()
        block=script[script.index('# Opt-in only:'):script.index('## When plume rise is enabled, confirm')]
        shell(block,dict(emissionsPrmVegetation='True',emissionsPrmVegetationConfig=self.cfg,
            emissionsPrmVegetationFile='unused.nc',doBburnPrm='True',mainScriptDir=self.f.r,
            PRMAreaDir=work,prmAreaFile='fire.nc',NamelistFile=n,StreamsFile=st,nCells=3,
            localInvariantFieldsFile=self.f.mesh,StartDate='2024-09-13_00:00:00',self_FCLengthHR=6),fc)
        self.assertEqual((fc/'fire.nc').resolve(),work/'prm_support/fire_size.nc')
        self.assertIn('prm_support/vegetation.nc',st.read_text())
        self.assertIn('config_prm_source_vegetation = true',n.read_text())

    def test_unknown_configuration_fails(self):
        self.settings['methdo']='finn_point'
        with self.assertRaisesRegex(ValueError,'Unknown'):self.run_it()
    def test_modified_output_rejected(self):
        self.run_it()
        with Dataset(self.out/'fire_size.nc','a') as d:d['firesize_biob_modis_avg'][0,0]=1
        with self.assertRaisesRegex(ValueError,'stale'):self.run_it()

if __name__=='__main__':unittest.main()
