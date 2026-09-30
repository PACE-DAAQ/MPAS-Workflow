#!/usr/bin/env python3
"""Make optional daily PRM categories from the same FINN records as fire size.

No emissions or meteorological fields are modified. The input manifest has CSV
columns date,path (ISO date, absolute FINN text/gzip path). Category crosswalk:
https://www2.acom.ucar.edu/facility/finn (GENVEG definitions).
The PRM grouping follows the existing model categories; this is an explicit
aggregation approximation, not a new calibration of heat flux or flaming fraction.
"""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,re
from pathlib import Path
from datetime import date,timedelta
import numpy as np

# 0 unknown; 1 tropical forest; 2 extratropical forest; 3 woody savanna/shrub;
# 4 grass/crop. FINN's temperate forests share the existing forest PRM treatment.
CROSSWALK={0:0,1:4,2:3,3:1,4:2,5:2,6:2,9:4}

def read_records(path,day,rejection_counts=None):
    opener=gzip.open if str(path).endswith('.gz') else open
    vals=[]
    with opener(path,'rt') as f:
        lines=(s.strip() for s in f if s.strip() and not s.lstrip().startswith('#'))
        header=re.split(r'[,\s]+',next(lines).strip(' ,'))
        required=['DAY','LATI','LONGI','GENVEG','AREA']
        cols=[header.index(k) for k in required]
        for line in lines:
            row=re.split(r'[,\s]+',line.strip(' ,'))
            if len(row)<=max(cols):raise ValueError(f'{path}: short record')
            v=[float(row[i].replace('D','E').replace('d','e')) for i in cols]
            if not np.all(np.isfinite(v)):raise ValueError(f'{path}: nonfinite source metadata')
            if v[0]!=day.timetuple().tm_yday:raise ValueError(f'{path}: DAY disagrees with manifest')
            if not(-90<=v[1]<=90 and -180<=v[2]<=360):raise ValueError('Invalid fire coordinates')
            if v[4]<0:
                # Match MPAS-Workflow FINN PRM moments(): finite AREA >= 0 only.
                if rejection_counts is not None:
                    rejection_counts['negative_area']=rejection_counts.get('negative_area',0)+1
                continue
            if v[3]!=int(v[3]) or int(v[3]) not in CROSSWALK:raise ValueError('Unknown FINN GENVEG')
            vals.append(v[1:])
    return np.asarray(vals,dtype=float).reshape(-1,4)

def aggregate(cell,genveg,area,ncells):
    cell=np.asarray(cell,dtype=int);genveg=np.asarray(genveg);area=np.asarray(area,float)
    if not(cell.shape==genveg.shape==area.shape):raise ValueError('Input shapes differ')
    if np.any(cell<0) or np.any(cell>=ncells):raise ValueError('Cell index out of range')
    if not np.all(np.isfinite(area)) or np.any(area<0):raise ValueError('Invalid fire area')
    if not np.all(np.isin(genveg,list(CROSSWALK))):raise ValueError('Unknown FINN GENVEG')
    category=np.array([CROSSWALK[int(x)] for x in genveg],int)
    weights=np.zeros((ncells,5),float)
    np.add.at(weights,(cell,category),area)
    known=weights[:,1:].sum(axis=1);total=weights.sum(axis=1)
    out=(weights[:,1:].argmax(axis=1)+1).astype('i4');out[known==0]=0
    # Ties use the lowest PRM category, deterministically; share exposes mixtures.
    share=np.divide(weights[:,1:].max(axis=1),known,out=np.zeros(ncells),where=known>0)
    coverage=np.divide(known,total,out=np.zeros(ncells),where=total>0)
    count=np.bincount(cell,minlength=ncells)
    mean=np.divide(total,count,out=np.zeros(ncells),where=count>0)
    return out,share,coverage,count,mean

def xyz(lat,lon):
    a,b=np.deg2rad(lat),np.deg2rad(lon)
    return np.column_stack((np.cos(a)*np.cos(b),np.cos(a)*np.sin(b),np.sin(a)))

def generate(mesh,manifest,prm_input,output):
    from netCDF4 import Dataset,chartostring
    from scipy.spatial import cKDTree
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    with open(manifest) as f:entries=list(csv.DictReader(f))
    days=[date.fromisoformat(e['date']) for e in entries]
    if not days or any(b-a!=timedelta(days=1) for a,b in zip(days,days[1:])):raise ValueError('Manifest must be daily, ordered, without gaps')
    with Dataset(mesh) as d:
        lat=np.rad2deg(np.asarray(d['latCell'][:],float));lon=np.rad2deg(np.asarray(d['lonCell'][:],float));ncells=len(lat)
    tree=cKDTree(xyz(lat,lon));provenance=[]
    tmp=output.with_name(output.name+'.partial')
    try:
        with Dataset(prm_input) as ref,Dataset(tmp,'w',format='NETCDF3_64BIT_OFFSET') as d:
            times=chartostring(ref['xtime'][:]);d.createDimension('Time',None);d.createDimension('nCells',ncells);d.createDimension('StrLen',64)
            xt=d.createVariable('xtime','S1',('Time','StrLen'))
            cat=d.createVariable('prm_source_vegetation','i4',('Time','nCells'));cat.units='1';cat.flag_values=np.arange(5,dtype='i4');cat.flag_meanings='unknown tropical_forest extratropical_forest woody_savanna_shrub grass_crop'
            sharev=d.createVariable('prm_source_dominant_share','f4',('Time','nCells'));sharev.units='1'
            cov=d.createVariable('prm_source_known_area_fraction','f4',('Time','nCells'));cov.units='1'
            countv=d.createVariable('prm_source_record_count','i4',('Time','nCells'))
            d.category_reference='https://www2.acom.ucar.edu/facility/finn'
            d.invalid_area_policy='Exclude negative AREA records, matching the existing FINN PRM moments filter; count exclusions in provenance'
            d.aggregation='Dominant PRM class by summed FINN AREA; ties lowest class; zero if no known positive area'
            d.mesh_coordinates_sha256=hashlib.sha256(np.column_stack((lat,lon)).astype('<f8').tobytes()).hexdigest()
            d.fire_size_reference=str(Path(prm_input).resolve())
            for t,(day,entry) in enumerate(zip(days,entries)):
                p=Path(entry['path']);rejected={};r=read_records(p,day,rejected);cell=tree.query(xyz(r[:,0],r[:,1]))[1]
                c,s,k,n,m=aggregate(cell,r[:,2],r[:,3],ncells)
                jj=np.where(np.char.startswith(times,day.isoformat()))[0]
                if len(jj)!=1:raise ValueError('Expected one matching daily fire-size record')
                expected=np.asarray(ref['firesize_biob_modis_avg'][int(jj[0]),:],float)
                if not np.allclose(m,expected,rtol=2e-5,atol=.5):
                    raise ValueError(f'{day}: source records do not reproduce staged fire-size mean; max delta {np.max(abs(m-expected))}')
                xt[t,:]=np.frombuffer(str(times[int(jj[0])]).ljust(64).encode(),dtype='S1');cat[t,:]=c;sharev[t,:]=s;cov[t,:]=k;countv[t,:]=n
                provenance.append(dict(date=day.isoformat(),path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),records=len(r),rejected_records=rejected,classified_cells=int((c>0).sum()),mixed_cells=int(((s>0)&(s<1)).sum())))
        tmp.replace(output)
    except BaseException:
        if tmp.exists():tmp.unlink()
        raise
    output.with_suffix(output.suffix+'.json').write_text(json.dumps({'crosswalk':CROSSWALK,'sources':provenance},indent=2))
    return provenance

def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def prepare(mesh,manifest,prm_input,output):
    """Reuse only if all inputs and the generated artifact remain identical."""
    with open(manifest) as f:entries=list(csv.DictReader(f))
    signature=dict(mesh=file_hash(mesh),manifest=file_hash(manifest),
                   fire_size=file_hash(prm_input),
                   sources=[file_hash(e['path']) for e in entries],
                   generator=file_hash(__file__))
    output=Path(output);receipt=output.with_suffix(output.suffix+'.validated.json')
    if output.exists():
        if not receipt.exists():raise ValueError('Existing output lacks validation receipt; choose a new output or regenerate explicitly')
        saved=json.loads(receipt.read_text())
        if saved['inputs']!=signature or saved['output']!=file_hash(output):
            raise ValueError('Existing vegetation input is stale or changed; regenerate explicitly')
        return
    generate(mesh,manifest,prm_input,output)
    receipt.write_text(json.dumps(dict(inputs=signature,output=file_hash(output)),indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for k in ['mesh','manifest','prm-input','output']:ap.add_argument('--'+k,required=True)
    ap.add_argument('--reuse-validated',action='store_true')
    a=ap.parse_args()
    fn=prepare if a.reuse_validated else generate
    fn(a.mesh,a.manifest,a.prm_input,a.output)
