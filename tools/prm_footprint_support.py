#!/usr/bin/env python3
"""Opt-in missing PRM support from FINN records in positive native source pixels.

Writes a NEW package; never changes emissions, defaults, or input files.
See docs/prm_footprint_support.md for the input contract and scientific limits.
"""
import argparse
import csv
from datetime import date
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from netCDF4 import Dataset, chartostring
from scipy.spatial import cKDTree
from pyproj import CRS, Transformer
from shapely.geometry import Polygon

from prm_source_vegetation import aggregate, file_hash, read_records, xyz


def values(variable):
    a = variable[:]
    if np.ma.getmaskarray(a).any() or not np.isfinite(a).all():
        raise ValueError(f'Missing/nonfinite values in {variable.name}')
    return np.asarray(a)


def edges(centers, latitude=False):
    """Regular cell-centred global raster, increasing coordinates only."""
    c = np.asarray(centers, float)
    if c.ndim != 1 or len(c) < 2 or not np.isfinite(c).all():
        raise ValueError('Invalid raster coordinates')
    step = np.diff(c)
    if step[0] <= 0 or not np.allclose(step, step[0], rtol=1e-4, atol=1e-6):
        raise ValueError('Expected increasing regular cell-centred raster')
    e = np.r_[c[0]-step[0]/2, (c[:-1]+c[1:])/2, c[-1]+step[-1]/2]
    expected = 180 if latitude else 360
    if not np.isclose(e[-1]-e[0], expected, atol=1e-3):
        raise ValueError('Only global cell-centred rasters are supported')
    if latitude and not np.allclose(e[[0,-1]], [-90,90], atol=1e-3):
        raise ValueError('Latitude bounds must span -90 to 90')
    if latitude: e[[0,-1]] = [-90,90]
    return e


def pixel_ids(records, ye, xe):
    lon = (records[:,1]-xe[0]) % 360 + xe[0]
    iy = np.searchsorted(ye, records[:,0], side='right')-1
    ix = np.searchsorted(xe, lon, side='right')-1
    iy = np.minimum(iy, len(ye)-2)  # north pole belongs to last row
    return iy, ix


def overlap_area(lat, lon, vertices, bounds, step=0.01):
    """Spherical MPAS great-circle edges; densified raster latitude edges.

    Gnomonic projection makes spherical great circles straight. This area is
    used ONLY to distinguish positive overlap, never as an emission weight.
    """
    crs = CRS.from_proj4(f'+proj=gnom +lat_0={lat} +lon_0={lon} +R=6371220 +units=m')
    tr = Transformer.from_crs(CRS.from_proj4('+proj=longlat +R=6371220'), crs, always_xy=True)
    def polygon(x, y):
        xx, yy = tr.transform(x, y)
        if not np.isfinite(xx).all() or not np.isfinite(yy).all():
            raise ValueError('Polygon exceeds local projection hemisphere')
        p = Polygon(np.column_stack([xx,yy]))
        if not p.is_valid or p.area <= 0: raise ValueError('Invalid polygon')
        return p
    west,east,south,north = bounds
    n = max(2, int(np.ceil((east-west)/step))+1)
    xx = np.linspace(west,east,n)
    raster = polygon(np.r_[xx,xx[::-1]], np.r_[np.full(n,south),np.full(n,north)])
    mesh = polygon(vertices[:,1], vertices[:,0])
    return mesh.intersection(raster).area


def has_overlap(lat,lon,vertices,bounds,min_area):
    a = overlap_area(lat,lon,vertices,bounds,0.02)
    b = overlap_area(lat,lon,vertices,bounds,0.01)
    if (a > min_area) != (b > min_area):
        raise ValueError('Overlap threshold is resolution-sensitive; inspect geometry')
    return b > min_area


def unique_records(records):
    # Exact metadata duplicates have no reliable independent identity here.
    # Reject rather than silently count twice or erase possibly distinct fires.
    if len(np.unique(records, axis=0)) != len(records):
        raise ValueError('Duplicate FINN metadata records; resolve source identity first')
    return records


def timestamps(d):
    ts = [str(x).strip() for x in chartostring(d['xtime'][:])]
    if len(set(ts)) != len(ts) or ts != sorted(ts): raise ValueError('Invalid timestamps')
    return ts


def generate(mesh, manifest, fire_size, vegetation, output_dir, min_overlap_m2=1.0, cells=None):
    if not np.isfinite(min_overlap_m2) or min_overlap_m2 <= 0:
        raise ValueError('Overlap threshold must be positive')
    out = Path(output_dir)
    if out.exists(): raise FileExistsError(out)
    with open(manifest) as stream:
        entries = list(csv.DictReader(stream))
    days = [date.fromisoformat(e['date']) for e in entries]
    if not days or days != sorted(set(days)): raise ValueError('Manifest dates must be unique and ordered')
    with Dataset(mesh) as d:
        lat,lon = [np.rad2deg(values(d[k]).astype(float)) for k in ['latCell','lonCell']]
        vl,vo = [np.rad2deg(values(d[k]).astype(float)) for k in ['latVertex','lonVertex']]
        vc,ne = [values(d[k]).astype(int) for k in ['verticesOnCell','nEdgesOnCell']]
    nc = len(lat)
    if cells is not None and (len(set(cells)) != len(cells) or any(i < 0 or i >= nc for i in cells)):
        raise ValueError("Invalid or repeated cell indices")
    import hashlib
    fingerprint = hashlib.sha256(np.column_stack([lat,lon]).astype('<f8').tobytes()).hexdigest()
    for i,n in enumerate(ne):
        if n < 3 or n > vc.shape[1] or np.any(vc[i,:n]<1) or np.any(vc[i,:n]>len(vl)):
            raise ValueError('Invalid MPAS connectivity')
    provenance = dict(method='positive-native-pixel overlap, missing support only',
        cells=cells, min_overlap_m2=min_overlap_m2, geometry='spherical gnomonic; raster parallels densified at 0.02/0.01 degrees',
        warning='Support records can serve multiple cells; NOT a conserved fire-area inventory or emission remap',
        inputs={str(Path(p).resolve()):file_hash(p) for p in [mesh,manifest,fire_size,vegetation]}, days=[])
    out.parent.mkdir(parents=True,exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=out.name+'.partial-',dir=out.parent))
    try:
        shutil.copy2(fire_size,work/'fire_size.nc');shutil.copy2(vegetation,work/'vegetation.nc')
        with Dataset(work/'fire_size.nc','r+') as fs, Dataset(work/'vegetation.nc','r+') as veg:
            ts = timestamps(fs)
            if ts != timestamps(veg): raise ValueError('Input timestamps differ')
            if getattr(veg,'mesh_coordinates_sha256','') != fingerprint: raise ValueError('Vegetation mesh mismatch')
            for d in [fs,veg]:
                if len(d.dimensions['nCells']) != nc: raise ValueError('Mesh size mismatch')
            for name in ['firesize_biob_modis_avg']:
                if fs[name].dimensions != ('Time','nCells'): raise ValueError('Unexpected fire-size dimensions')
            for name in ['prm_source_vegetation','prm_source_dominant_share','prm_source_known_area_fraction','prm_source_record_count']:
                if veg[name].dimensions != ('Time','nCells'): raise ValueError('Unexpected vegetation dimensions')
            for day,e in zip(days,entries):
                match = [i for i,t in enumerate(ts) if t[:10] == day.isoformat()]
                if len(match)!=1: raise ValueError('Date missing/ambiguous in support inputs')
                t = match[0]; rejected={}
                records = unique_records(read_records(e['finn'],day,rejected))
                with Dataset(e['native']) as d:
                    yy,xx = values(d['lat']),values(d['lon']);ye,xe = edges(yy,True),edges(xx)
                    var = d[e['variable']]
                    if var.dimensions != ('time','lat','lon') or var.shape[0]!=1:
                        raise ValueError('Native source requires one daily time,lat,lon record')
                    from netCDF4 import num2date
                    tv=d['time']; when=num2date(values(tv)[0],tv.units,calendar=getattr(tv,'calendar','standard'))
                    if (when.year,when.month,when.day)!=(day.year,day.month,day.day): raise ValueError('Native source date mismatch')
                    flux=np.ma.filled(var[0,:,:].astype(float),np.nan)
                old=np.ma.filled(fs['firesize_biob_modis_avg'][t,:].astype(float),np.nan)
                if not np.isfinite(old).all() or (old<0).any(): raise ValueError('Invalid existing support')
                # Staged target source is required: do not fill cells with no emissions.
                with Dataset(e['target']) as d:
                    if len(d.dimensions['nCells'])!=nc: raise ValueError('Target mesh size mismatch')
                    # Require explicit mesh fingerprint, or direct coordinate identity.
                    if e.get('target_scrip'):
                        # Legacy fingerprints may predate today's mesh hash. Verify
                        # the original SCRIP centres AND corners, not cell count.
                        with Dataset(e['target_scrip']) as grid:
                            tag=getattr(d,'mesh_fingerprint','')
                            if not tag or tag!=getattr(grid,'mesh_fingerprint',None):
                                raise ValueError('Target/SCRIP fingerprint mismatch')
                            for key in ['grid_center_lat','grid_center_lon','grid_corner_lat','grid_corner_lon']:
                                if getattr(grid[key],'units','')!='radians': raise ValueError('SCRIP coordinates must be radians')
                            if not np.allclose(values(grid['grid_center_lat']),np.deg2rad(lat),rtol=0,atol=1e-10) or not np.allclose(np.angle(np.exp(1j*(values(grid['grid_center_lon'])-np.deg2rad(lon)))),0,rtol=0,atol=1e-10):
                                raise ValueError('SCRIP cell coordinate/order mismatch')
                            gl=values(grid['grid_corner_lat']);go=values(grid['grid_corner_lon'])
                            for ci,nn in enumerate(ne):
                                vv=vc[ci,:nn]-1
                                if not np.allclose(gl[ci,:nn],np.deg2rad(vl[vv]),rtol=0,atol=1e-10) or not np.allclose(np.angle(np.exp(1j*(go[ci,:nn]-np.deg2rad(vo[vv])))),0,rtol=0,atol=1e-10):
                                    raise ValueError('SCRIP vertex geometry mismatch')
                    elif 'latCell' in d.variables and 'lonCell' in d.variables:
                        if not np.allclose(values(d['latCell']),np.deg2rad(lat),rtol=0,atol=1e-12) or not np.allclose(values(d['lonCell']),np.deg2rad(lon),rtol=0,atol=1e-12):
                            raise ValueError('Target coordinate/order mismatch')
                    else:
                        from mpas_emissions.mesh import MpasMesh
                        if getattr(d,'mesh_fingerprint','') != MpasMesh.open(mesh).fingerprint:
                            raise ValueError('Target mesh fingerprint mismatch')
                    tt=timestamps(d); jj=[i for i,s in enumerate(tt) if s[:10]==day.isoformat()]
                    if not jj: raise ValueError('Target date absent')
                    v=d[e['target_variable']]
                    if v.dimensions!=('Time','nCells'): raise ValueError('Unexpected target dimensions')
                    target=np.ma.filled(v[jj,:].astype(float),np.nan)
                    if not np.isfinite(target).all() or (target<0).any(): raise ValueError('Invalid target emissions')
                    active=(target>0).any(axis=0)
                if cells is not None:
                    scope=np.zeros(nc,bool);scope[cells]=True;active &= scope
                targets=np.flatnonzero((old==0)&active)
                tree=cKDTree(xyz(lat[targets],lon[targets])) if len(targets) else None
                # Conservative search radius includes the furthest vertex of every target.
                radius=0.
                for i in targets:
                    vv=vc[i,:ne[i]]-1
                    radius=max(radius,float(np.linalg.norm(xyz(vl[vv],vo[vv])-xyz([lat[i]],[lon[i]]),axis=1).max()))
                iy,ix=pixel_ids(records,ye,xe);groups={}
                for ri,(y,x) in enumerate(zip(iy,ix)):
                    if records[ri,3]>0 and np.isfinite(flux[y,x]) and flux[y,x]>0:
                        groups.setdefault((int(y),int(x)),[]).append(ri)
                matches={};pixels={}
                for (y,x),ids in sorted(groups.items()):
                    if tree is None: break
                    bounds=[float(xe[x]),float(xe[x+1]),float(ye[y]),float(ye[y+1])]
                    # Half latitude width + half longitude width bounds angular distance.
                    pr=np.deg2rad((xe[x+1]-xe[x]+ye[y+1]-ye[y])/2)
                    candidates=tree.query_ball_point(xyz([yy[y]],[xx[x]])[0],min(2.,radius+pr+1e-8))
                    for c in sorted(candidates):
                        i=int(targets[c]);vv=vc[i,:ne[i]]-1
                        if has_overlap(lat[i],lon[i],np.column_stack([vl[vv],vo[vv]]),bounds,min_overlap_m2):
                            matches.setdefault(i,set()).update(ids);pixels.setdefault(i,[]).append([y,x])
                changes=[]
                for i,ids in sorted(matches.items()):
                    ids=sorted(ids);r=records[ids]
                    c,s,k,n,m=aggregate(np.zeros(len(r),int),r[:,2],r[:,3],1)
                    # Unknown categories cannot establish a complete support package.
                    if c[0]==0: continue
                    fs['firesize_biob_modis_avg'][t,i]=m[0]
                    for key,val in [('prm_source_vegetation',c[0]),('prm_source_dominant_share',s[0]),('prm_source_known_area_fraction',k[0]),('prm_source_record_count',n[0])]: veg[key][t,i]=val
                    changes.append(dict(cell=i,mean_area_m2=float(m[0]),category=int(c[0]),native_pixels=pixels[i],accepted_record_indices=ids,records_lat_lon_genveg_area=r.tolist()))
                provenance['days'].append(dict(date=str(day),inputs={str(Path(e[k]).resolve()):file_hash(e[k]) for k in ['finn','native','target']+(['target_scrip'] if e.get('target_scrip') else [])},rejected=rejected,eligible_cells=len(targets),filled_cells=len(changes),unfilled_cells=len(targets)-len(changes),changes=changes))
            for d in [fs,veg]: d.footprint_support_method=provenance['method'];d.footprint_support_warning=provenance['warning']
        provenance['outputs']={p.name:file_hash(p) for p in work.iterdir()}
        (work/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
        work.rename(out)
    except BaseException:
        shutil.rmtree(work);raise
    return provenance


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['mesh','manifest','fire-size','vegetation','output-dir']: p.add_argument('--'+key,required=True)
    p.add_argument('--min-overlap-m2',type=float,default=1.)
    p.add_argument('--cells',type=lambda x:[int(i) for i in x.split(',')],help='Optional zero-based cell indices; otherwise all eligible cells')
    a=p.parse_args();generate(a.mesh,a.manifest,a.fire_size,a.vegetation,a.output_dir,a.min_overlap_m2,a.cells)
