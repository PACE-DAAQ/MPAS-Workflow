#!/usr/bin/env python3
"""Prepare a paired PRM package from one YAML; manifests are internal artifacts."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile

import yaml
from netCDF4 import Dataset, chartostring

from prm_source_vegetation import generate as vegetation_generate, file_hash


def resolve(pattern, root, day):
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('Expected a nonempty source path pattern')
    p = Path(day.strftime(pattern))
    if not p.is_absolute(): p = root / p
    return p.resolve(strict=True)


def _prepare(config, mesh, fire_size, output):
    config = Path(config).resolve(strict=True)
    cfg = yaml.safe_load(config.read_text())
    if not isinstance(cfg, dict): raise ValueError('PRM configuration must be a mapping')
    allowed = {'method','native root','finn','footprint'}
    if set(cfg)-allowed: raise ValueError('Unknown PRM configuration keys')
    method = cfg.get('method','finn_point')
    if method not in ('finn_point','footprint_overlap'): raise ValueError('Invalid PRM method')
    root = Path(cfg.get('native root', str(config.parent)))
    if not root.is_absolute(): root = config.parent / root
    finn = cfg.get('finn',{})
    if not isinstance(finn,dict): raise ValueError('finn must be a mapping')
    if set(finn)-{'pattern','overrides'}: raise ValueError('Unknown FINN configuration keys')
    overrides = finn.get('overrides',{})
    if not isinstance(overrides,dict): raise ValueError('FINN overrides must be a mapping with quoted ISO dates')
    with Dataset(fire_size) as d:
        times = [str(t).strip() for t in chartostring(d['xtime'][:])]
    days = [datetime.strptime(t,'%Y-%m-%d_%H:%M:%S') for t in times]
    if not days or len({d.date() for d in days})!=len(days): raise ValueError('Expected one fire-size record per day')
    if set(overrides)-{str(d.date()) for d in days}: raise ValueError('FINN override outside fire-size coverage')
    rows=[]; paths=[config,Path(mesh),Path(fire_size)]
    for day in days:
        source=resolve(overrides.get(str(day.date()),finn.get('pattern')),root,day)
        rows.append({'date':str(day.date()),'path':str(source)});paths.append(source)
    fp=cfg.get('footprint',{})
    if not isinstance(fp,dict): raise ValueError('footprint must be a mapping')
    if method=='finn_point' and fp: raise ValueError('Footprint settings require footprint_overlap')
    if set(fp)-{'native pattern','variable','target','target variable','target scrip','cells','min overlap m2'}:
        raise ValueError('Unknown footprint configuration keys')
    overlap=[]
    if method=='footprint_overlap':
        for day,row in zip(days,rows):
            r={'date':row['date'],'finn':row['path'],
               'native':str(resolve(fp['native pattern'],root,day)),
               'variable':fp['variable'],
               'target':str(resolve(fp['target'],root,day)),
               'target_variable':fp['target variable']}
            paths.extend([Path(r['native']),Path(r['target'])])
            if fp.get('target scrip'):
                r['target_scrip']=str(resolve(fp['target scrip'],root,day));paths.append(Path(r['target_scrip']))
            overlap.append(r)
    scripts=[Path(__file__),Path(__file__).with_name('prm_source_vegetation.py')]
    if method=='footprint_overlap': scripts.append(Path(__file__).with_name('prm_footprint_support.py'))
    signature={str(p.resolve()):file_hash(p) for p in set(paths+scripts)}
    output=Path(output)
    if output.exists():
        receipt=json.loads((output/'validated.json').read_text())
        current={p.name:file_hash(p) for p in output.iterdir() if p.name!='validated.json'}
        if receipt['inputs']!=signature or receipt['outputs']!=current:
            raise ValueError('PRM package is stale or modified; choose a new output directory')
        return
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.prm-',dir=output.parent))
    try:
        def manifest(path, records):
            with open(path,'w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
        manifest(stage/'finn_manifest.csv',rows)
        vegetation_generate(mesh,stage/'finn_manifest.csv',fire_size,stage/'point.nc')
        package=stage/'package'
        if method=='footprint_overlap':
            from prm_footprint_support import generate
            manifest(stage/'footprint_manifest.csv',overlap)
            generate(mesh,stage/'footprint_manifest.csv',fire_size,stage/'point.nc',package,
                     fp.get('min overlap m2',1.),fp.get('cells'))
            shutil.copy2(stage/'footprint_manifest.csv',package/'footprint_manifest.csv')
            shutil.copy2(stage/'point.nc',package/'point_vegetation.nc')
            provenance=json.loads((package/'provenance.json').read_text())
            names={str(stage/'point.nc'):'package:point_vegetation.nc',
                   str(stage/'footprint_manifest.csv'):'package:footprint_manifest.csv'}
            provenance['inputs']={names.get(k,k):v for k,v in provenance['inputs'].items()}
            (package/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
        else:
            package.mkdir();shutil.copy2(fire_size,package/'fire_size.nc')
            shutil.copy2(stage/'point.nc',package/'vegetation.nc')
        shutil.copy2(stage/'point.nc.json',package/'point_provenance.json')
        shutil.copy2(stage/'finn_manifest.csv',package/'finn_manifest.csv')
        shutil.copy2(config,package/'config.yaml')
        receipt={'inputs':signature,'outputs':{p.name:file_hash(p) for p in package.iterdir()}}
        (package/'validated.json').write_text(json.dumps(receipt,indent=2)+'\n')
        package.rename(output)
    finally:
        shutil.rmtree(stage)


def prepare(config, mesh, fire_size, output):
    # Concurrent cycle preparation must not replace or partially read a package.
    import fcntl
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with open(output.with_name(output.name+'.lock'),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _prepare(config,mesh,fire_size,output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['config','mesh','fire-size','output']:p.add_argument('--'+key,required=True)
    a=p.parse_args();prepare(a.config,a.mesh,a.fire_size,a.output)
