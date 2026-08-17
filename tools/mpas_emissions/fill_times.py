#!/usr/bin/env python3
"""Fill missing times in an already-MPAS-grid emissions file by interpolation.

Useful during migration: legacy/prebuilt CEDS/QFED/GFAS files can be repaired
without rerunning horizontal regridding. Output is CDF-5 and source slices are
read lazily, so multi-GB annual files do not need to fit in memory.
"""
from __future__ import annotations
import argparse
from collections import OrderedDict
from datetime import timedelta, datetime
import numpy as np
from netCDF4 import Dataset, chartostring
from .stream_io import MpasEmissionStreamWriter
from .time_axis import SourceRecord, make_schedule, parse_datetime, resolve_brackets


def _times(ds):
    s=chartostring(ds.variables["xtime"][:])
    return [datetime.strptime(str(x).strip().replace("\x00",""), "%Y-%m-%d_%H:%M:%S") for x in s]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("input"); p.add_argument("output"); p.add_argument("--start",required=True); p.add_argument("--end",required=True)
    p.add_argument("--step-hours",type=float,required=True); p.add_argument("--max-gap-hours",type=float,default=None)
    p.add_argument("--method",choices=["linear","nearest","hold"],default="linear")
    a=p.parse_args()
    src=Dataset(a.input)
    try:
        times=_times(src); n=int(src.dimensions["nCells"].size)
        names=[k for k,v in src.variables.items() if tuple(v.dimensions)==("Time","nCells")]
        rec=[SourceRecord(t,a.input,i) for i,t in enumerate(times)]
        targets=make_schedule(parse_datetime(a.start),parse_datetime(a.end),timedelta(hours=a.step_hours))
        br=resolve_brackets(rec,targets,method=a.method,max_gap=None if a.max_gap_hours is None else timedelta(hours=a.max_gap_hours))
        cache: OrderedDict[tuple[str,int],np.ndarray]=OrderedDict()
        def get(name,idx):
            key=(name,idx)
            if key in cache:
                x=cache.pop(key); cache[key]=x; return x
            x=np.asarray(src.variables[name][idx,:],dtype=np.float64)
            cache[key]=x
            while len(cache)>max(4,2*len(names)): cache.popitem(last=False)
            return x
        with MpasEmissionStreamWriter(a.output,n_cells=n,field_names=names,
             attrs={"time_fill_source":str(a.input),"time_fill_method":a.method}) as w:
            for b in br:
                vals={}
                for name in names:
                    x=get(name,b.before.time_index)
                    if b.before==b.after: vals[name]=x
                    else: vals[name]=(1-b.alpha_after)*x+b.alpha_after*get(name,b.after.time_index)
                w.append(b.target,vals)
    finally:
        src.close()

if __name__=="__main__": main()
