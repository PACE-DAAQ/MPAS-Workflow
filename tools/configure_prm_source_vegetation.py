#!/usr/bin/env python3
"""Validate optional PRM input before adding the stream and namelist switch."""
import argparse
import hashlib
from datetime import datetime,timedelta
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import numpy as np
from netCDF4 import Dataset, chartostring


def configure(source, fire_size, namelist, streams, ncells, mesh, start=None, hours=None):
    source=Path(source).resolve(strict=True)
    with Dataset(mesh) as grid:
        coords=np.column_stack([np.rad2deg(np.asarray(grid[k][:],float)) for k in ['latCell','lonCell']])
        fingerprint=hashlib.sha256(coords.astype('<f8').tobytes()).hexdigest()
    with Dataset(source) as d, Dataset(fire_size) as ref:
        if getattr(d,'mesh_coordinates_sha256','')!=fingerprint:
            raise ValueError('Vegetation mesh coordinates/order mismatch')
        if len(d.dimensions['nCells'])!=ncells or len(ref.dimensions['nCells'])!=ncells:
            raise ValueError('PRM input mesh size mismatch')
        times=[str(t).strip() for t in chartostring(d['xtime'][:])]
        rt=[str(t).strip() for t in chartostring(ref['xtime'][:])]
        if not times or len(set(times))!=len(times) or times!=sorted(times):
            raise ValueError('Vegetation timestamps must be unique and ordered')
        # Full reference coverage avoids silent carry-forward past the last record.
        if times!=rt:raise ValueError('Vegetation timestamps must exactly cover staged fire-size timestamps')
        dates=[datetime.strptime(t,'%Y-%m-%d_%H:%M:%S') for t in times]
        if any(b-a!=timedelta(days=1) for a,b in zip(dates,dates[1:])):
            raise ValueError('Daily PRM records contain a gap')
        if start is not None:
            first=datetime.strptime(start,'%Y-%m-%d_%H:%M:%S')
            last=first+timedelta(hours=hours)
            if first<dates[0] or last>=dates[-1]+timedelta(days=1):
                raise ValueError('PRM input does not cover forecast period')
        v=d['prm_source_vegetation']
        if v.dimensions!=('Time','nCells'):raise ValueError('Unexpected vegetation layout')
        for i in range(len(times)):
            c=v[i,:]
            if np.ma.getmaskarray(c).any() or not np.isin(c,[0,1,2,3,4]).all():
                raise ValueError('Missing/invalid vegetation category')
    n=Path(namelist);text=n.read_text()
    if not re.search(r'(?im)^\s*&plumerisemodel\b',text):
        raise ValueError('Missing plumerisemodel namelist group')
    text=re.sub(r'(?im)^\s*config_prm_source_vegetation\s*=.*\n','',text)
    text=re.sub(r'(?im)^(\s*&plumerisemodel\b[^\n]*\n)',r'\1    config_prm_source_vegetation = true\n',text)
    # Hourly polling selects the correct UTC day even for non-midnight starts.
    text,count=re.subn(r'(?im)^(\s*config_prm_lowbc_interval\s*=).*$',r'\1 00_01:00:00',text)
    if count!=1:raise ValueError('Expected one PRM lowbc interval setting')
    tree=ET.parse(streams);root=tree.getroot()
    for node in list(root):
        if node.get('name')=='prm_source_vegetation':root.remove(node)
    node=ET.SubElement(root,'stream',name='prm_source_vegetation',type='input',filename_template=str(source),input_interval='none')
    ET.SubElement(node,'var',name='prm_source_vegetation')
    # All validation precedes edits.
    tree.write(streams,encoding='unicode');n.write_text(text)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['input','fire-size','namelist','streams','mesh']:p.add_argument('--'+k,required=True)
    p.add_argument('--ncells',type=int,required=True)
    p.add_argument('--start',required=True)
    p.add_argument('--hours',type=float,required=True)
    a=p.parse_args();configure(a.input,a.fire_size,a.namelist,a.streams,a.ncells,a.mesh,a.start,a.hours)
