"""Run with python -m unittest discover -s test -p test_chemistry_ic_regressions.py."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from initialize.applications.InitIC import InitIC
from initialize.config.Component import Component
from initialize.config.Config import Config
from initialize.config.TaskFamily import CylcTaskFamily
from tools.mpas_inputs import resource

ROOT = Path(__file__).resolve().parents[1]

class ResourceTests(unittest.TestCase):
    def test_unset_candidate_does_not_hide_later_file_or_cache(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            root = Path(d)
            good = root / 'good.nc'
            good.write_text('data')
            args = dict(local_candidates=['${UNSET}/input.nc', str(good)],
                        cache_dir=root / 'cache', output_name='input.nc')
            self.assertEqual(resource.obtain(**args)[0], good)
            args['local_candidates'] = ['${UNSET}/input.nc']
            args['cache_dir'].mkdir()
            cached = args['cache_dir'] / 'input.nc'
            cached.write_text('cached')
            self.assertEqual(resource.obtain(**args)[0], cached)

    def test_unknown_date_field_still_rejected(self):
        with self.assertRaises(KeyError):
            resource.expand('/data/{typo}.nc', source_year=2024)

    def test_concurrent_downloads_own_temporary_files(self):
        with tempfile.TemporaryDirectory() as d:
            barrier = threading.Barrier(2)
            paths = []
            def download(cmd, check):
                path = Path(cmd[cmd.index('-o') + 1])
                paths.append(path)
                path.write_text('complete')
                barrier.wait(timeout=10)
            with patch.object(resource.subprocess, 'run', side_effect=download):
                def obtain(_):
                    return resource.obtain(local_candidates=[], url_candidates=['https://example.test/a'],
                                           cache_dir=d, output_name='monthly.nc')
                with ThreadPoolExecutor(2) as pool:
                    results = list(pool.map(obtain, range(2)))
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(r[0].read_text() == 'complete' for r in results))
            self.assertFalse(list(Path(d).glob('*.part')))

    def test_failed_download_does_not_publish_partial(self):
        with tempfile.TemporaryDirectory() as d:
            def fail(cmd, check):
                Path(cmd[cmd.index('-o') + 1]).write_text('partial')
                raise subprocess.CalledProcessError(1, cmd)
            with patch.object(resource.subprocess, 'run', side_effect=fail):
                with self.assertRaises(FileNotFoundError):
                    resource.obtain(local_candidates=[], url_candidates=['https://example.test/a'],
                                    cache_dir=d, output_name='monthly.nc')
            self.assertFalse(list(Path(d).iterdir()))

class GraphTests(unittest.TestCase):
    def test_empty_and_chemistry_edges_are_wrapped(self):
        # Run the real export method, including TaskFamily's internally generated edges.
        for mode in ('off', 'prebuilt', 'workflow'):
            for offsets in ([0], [0, 6]):
                with self.subTest(mode=mode, offsets=offsets):
                    app = InitIC.__new__(InitIC)
                    app._vtable = {'chemistry mode': mode}
                    app._tasks, app._dependencies, app._queues = [], [], []
                    app._InitIC__used = True
                    app._InitIC__task = SimpleNamespace(job=lambda:'', directives=lambda:'')
                    app.tf = CylcTaskFamily('InitIC')
                    app.baseTask = 'ExternalAnalysisToMPAS'
                    app.meshes = {'Outer':SimpleNamespace(name='60km', nCells=163842, meshRatio=1)}
                    class EA(dict):
                        WorkDir = 'EA'
                    app.ea = EA(ExternalAnalysesDirOuter='IC', externalanalyses__filePrefixOuter='init')
                    app.emissions = None
                    app.workflow = {'AnalysisTimes': '+PT6H/PT6H'}
                    with patch.object(Component, 'export'):
                        app.export(offsets)
                    text = ''.join(app._dependencies)
                    inside = False
                    for line in text.splitlines():
                        if '"""' in line:
                            inside = not inside
                        elif '=>' in line:
                            self.assertTrue(inside, line)
                    self.assertFalse(inside)
                    self.assertIn('R1 = """', text)
                    self.assertIn('+PT6H/PT6H = """', text)
                    self.assertEqual('PrepareChemIC-0hr =>' in text, mode == 'workflow')

    def test_unsupported_suite_rejects_workflow_mode(self):
        conf = Config()
        conf._table = {'initic': {'chemistry mode':'workflow'}}
        with self.assertRaisesRegex(ValueError, 'not supported by this suite'):
            InitIC(conf, None, {}, None)

@unittest.skipUnless(shutil.which('tcsh'), 'tcsh required')
class StreamsTests(unittest.TestCase):
    def test_all_init_placeholders_resolve_for_each_variant(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Adapt GNU sed -i to BSD sed for macOS tests; production uses GNU sed.
            if os.uname().sysname == 'Darwin':
                shim = root/'sed'
                shim.write_text('#!/bin/sh\nif [ "$1" = "-i" ]; then shift; exec /usr/bin/sed -i "" "$@"; fi\nexec /usr/bin/sed "$@"\n')
                shim.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'])
            for variant in ['cntl'] + [f'pert{i:02d}' for i in range(1,9)]:
                text = (ROOT/'config/mpas/initic/streams.init_atmosphere.gocart2g').read_text()
                for key, value in [('meshRatio','1'),('nCells','163842'),('PRECISION','single')]:
                    text = text.replace('{{'+key+'}}',value)
                (root/'streams').write_text(text)
                script = f'set StreamsFile = streams\nset streamsVariant = {variant}\nsource {ROOT}/bin/SetStreamsVariant.csh\n'
                result = subprocess.run(['tcsh','-f'],input=script,text=True,cwd=root,env=env,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn('Undefined variable',result.stderr)
                self.assertNotIn('{{',(root/'streams').read_text())

    def test_init_staging_uses_selected_templates_and_validates_inputs(self):
        source = (ROOT/'bin/ExternalAnalysisToMPAS.csh').read_text()
        staging = source[source.index('set initTemplate ='):source.index('# Run the executable')]
        for mode, missing in [('off',None), ('prebuilt',None), ('workflow',None),
                              ('prebuilt','background'), ('prebuilt','intermediate')]:
            with self.subTest(mode=mode, missing=missing), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                for name in ('chem','background','emissions','work'):
                    (root/name).mkdir()
                if missing != 'intermediate':
                    (root/'chem/MERRA2:2024-07-01_00').write_text('intermediate')
                for name in ('BACKGROUND_ptrop.dat','BACKGROUND_dms.dat','BACKGROUND_sulf.dat'):
                    if missing == 'background' and name == 'BACKGROUND_sulf.dat':
                        continue
                    (root/'background'/name).write_text('background')
                (root/'emissions/emissions.nc').write_text('emissions')
                if os.uname().sysname == 'Darwin':
                    shim=root/'sed'
                    shim.write_text('#!/bin/sh\nif [ "$1" = "-i" ]; then shift; exec /usr/bin/sed -i "" "$@"; fi\nexec /usr/bin/sed "$@"\n')
                    shim.chmod(0o755)
                variables = {
                    'StreamsFileInit':'streams.init_atmosphere',
                    'NamelistFileInit':'namelist.init_atmosphere',
                    'StreamsFile':'streams.atmosphere',
                    'initicChemistryMode':mode,
                    'ExperimentDirectory':str(root),
                    'initicChemistryWorkDir':'chem',
                    'initicChemistryPrebuiltDir':str(root/'chem'),
                    'initicChemistryBackgroundDir':str(root/'background'),
                    'initicEmissionMode':'prebuilt',
                    'EmissionDir':str(root/'emissions'),
                    'ModelConfigDir':str(ROOT/'config/mpas'),
                    'mainScriptDir':str(ROOT),
                    'ArgNCells':'163842', 'ArgRatio':'1', 'model__precision':'single',
                    'thisValidDate':'2024070100', 'thisMPASNamelistDate':'2024-07-01_00:00:00',
                    'externalanalyses__UngribPrefix':'GFS',
                }
                script='\n'.join(f'setenv {k} "{v}"' for k,v in variables.items())+'\n'+staging
                result=subprocess.run(['tcsh','-f'],input=script,text=True,cwd=root/'work',
                                      env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH']),capture_output=True)
                failure=root/'work/FAIL'
                if missing:
                    self.assertTrue(failure.exists(),result.stderr)
                    self.assertIn('missing or empty',failure.read_text())
                    continue
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertFalse(failure.exists(),result.stderr)
                nml=(root/'work/namelist.init_atmosphere').read_text()
                streams=(root/'work/streams.init_atmosphere').read_text()
                self.assertEqual('&preproc_chemistry' in nml,mode!='off')
                self.assertNotIn('{{',streams)
                self.assertNotIn('{{',nml)
                self.assertFalse((root/'work/streams.init_atmosphere.gocart2g').exists())

if __name__ == '__main__':
    unittest.main()
