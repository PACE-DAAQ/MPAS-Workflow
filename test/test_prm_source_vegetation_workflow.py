import json
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from netCDF4 import Dataset
import test_prm_source_vegetation_generator as fixtures
from prm_source_vegetation import prepare
from configure_prm_source_vegetation import configure


class Workflow(unittest.TestCase):
    setUp = fixtures.Generator.setUp
    reference = fixtures.Generator.reference
    run_generator = fixtures.Generator.run_generator
    def configs(self):
        self.nml=self.root/'namelist.atmosphere'
        self.xml=self.root/'streams.atmosphere'
        self.nml.write_text('&plumerisemodel\n config_prm_lowbc_interval = 01_00:00:00\n/\n')
        self.xml.write_text('<streams><stream name="other" type="input"/></streams>')

    def stage(self):
        configure(self.output,self.ref,self.nml,self.xml,2,self.mesh)

    def test_enable_and_idempotence(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        self.stage();self.stage()
        self.assertEqual(self.nml.read_text().count('config_prm_source_vegetation'),1)
        self.assertIn('00_01:00:00',self.nml.read_text())
        root=ET.parse(self.xml).getroot()
        self.assertEqual(len(root.findall("stream[@name='prm_source_vegetation']")),1)
        self.assertIsNotNone(root.find("stream[@name='other']"))

    def test_invalid_category_no_edits(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        before=(self.nml.read_bytes(),self.xml.read_bytes())
        with Dataset(self.output,'a') as d:d['prm_source_vegetation'][0,0]=99
        with self.assertRaisesRegex(ValueError,'category'):self.stage()
        self.assertEqual(before,(self.nml.read_bytes(),self.xml.read_bytes()))

    def test_wrong_mesh_order(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        with Dataset(self.mesh,'a') as d:d['lonCell'][:]=d['lonCell'][:][::-1]
        with self.assertRaisesRegex(ValueError,'coordinates/order'):self.stage()

    def test_timestamp_mismatch(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        self.reference(['2024-09-10_12:00:00'])
        with self.assertRaisesRegex(ValueError,'timestamps'):self.stage()

    def test_reuse_and_changed_source_rejected(self):
        self.reference(['2024-09-10_00:00:00'])
        prepare(self.mesh,self.manifest,self.ref,self.output)
        timestamp=self.output.stat().st_mtime_ns
        prepare(self.mesh,self.manifest,self.ref,self.output)
        self.assertEqual(self.output.stat().st_mtime_ns,timestamp)
        with (self.root/'fires.txt').open('a') as f:f.write('\n')
        with self.assertRaisesRegex(ValueError,'stale'):prepare(self.mesh,self.manifest,self.ref,self.output)

    def test_forecast_beyond_input_rejected(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        with self.assertRaisesRegex(ValueError,'forecast period'):
            configure(self.output,self.ref,self.nml,self.xml,2,self.mesh,'2024-09-10_18:00:00',12)

    def test_enabled_shell_path(self):
        self.reference(['2024-09-10_00:00:00']);self.run_generator();self.configs()
        repo=Path(__file__).resolve().parents[1]
        (self.root/'config').mkdir()
        (self.root/'config/environmentEmissions.csh').write_text('# test uses active NPL Python\n')
        (self.root/'tools').symlink_to(repo/'tools',target_is_directory=True)
        values=dict(emissionsPrmVegetation='True',doBburnPrm='True',mainScriptDir=self.root,
                    PRMAreaDir=self.root,emissionsPrmVegetationFile=self.output.name,
                    prmAreaFile=self.ref,NamelistFile=self.nml,StreamsFile=self.xml,
                    nCells=2,localInvariantFieldsFile=self.mesh,StartDate='2024-09-10_00:00:00',self_FCLengthHR=6)
        prefix=''.join(f'set {k} = "{v}"\n' for k,v in values.items())
        text=(repo/'bin/Forecast.csh').read_text()
        block=text[text.index('# Opt-in only:'):text.index('## When plume rise is enabled, confirm')]
        # Ensure python3 resolves to the same dependency-equipped interpreter.
        import os,sys
        env=dict(os.environ,PATH=str(Path(sys.executable).parent)+':'+os.environ['PATH'])
        subprocess.run(['tcsh','-f'],input=prefix+block,text=True,cwd=self.root,env=env,check=True,capture_output=True)
        self.assertIn('config_prm_source_vegetation = true',self.nml.read_text())

    def test_disabled_shell_path_does_not_touch_files(self):
        # Execute the actual Forecast opt-in block, not a reimplementation.
        self.configs();before=(self.nml.read_bytes(),self.xml.read_bytes())
        s=(Path(__file__).resolve().parents[1]/'bin/Forecast.csh').read_text()
        block=s[s.index('# Opt-in only:'):s.index('## When plume rise is enabled, confirm')]
        for prefix in ('','set emissionsPrmVegetation = "False"\n'):
            subprocess.run(['tcsh','-f'],input=prefix+block,text=True,cwd=self.root,check=True,capture_output=True)
        self.assertEqual(before,(self.nml.read_bytes(),self.xml.read_bytes()))


if __name__=='__main__':unittest.main()
