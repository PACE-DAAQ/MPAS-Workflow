"""MERRA2-GMI -> WPS-intermediate converter used by PrepareChemIC.

Adapted from the project MERRA-IC ``run_processing.py`` supplied with issue #2
integration material.  MPAS-Workflow's wrapper validates all required source
fields before calling this converter, so missing chemistry cannot silently
become zero in normal workflow use.
"""
import os
import sys
import yaml
import struct
import numpy as np
from netCDF4 import Dataset
from datetime import datetime, timedelta

def write_fortran_record(file_handle, *args):
    """
    Simulates a Fortran 'unformatted' write for WPS Intermediate files.
    """
    payload = b""
    for val, fmt in args:
        if 's' in fmt:
            length = int(fmt.replace('s', ''))
            payload += str(val).ljust(length)[:length].encode('ascii')
        elif fmt == '?':
            payload += struct.pack('>i', 1 if val else 0)
        elif fmt == 'b':
            payload += val
        else:
            payload += struct.pack(f'>{fmt}', val)

    record_size = len(payload)
    file_handle.write(struct.pack('>i', record_size))
    file_handle.write(payload)
    file_handle.write(struct.pack('>i', record_size))

def run_conversion(config_path='config.yaml'):
    # 1. LOAD CONFIGURATION
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    p = config['control_params']
    species_map = config['species_map']

    # 2. SETUP TIME PARAMETERS
    start_dt = datetime(p['start_year'], p['start_month'], p['start_day'], p['start_hour'])
    total_intervals = p['num_days'] * (24 // p['interval_hr'])

    # Constants for WPS Header
    IFV, IPROJ = 5, 0
    EARTH_RADIUS = 6367470 * 0.001
    IS_WIND_EARTH_REL = False
    STARTLOC = 'SWCORNER'

    active_handles = {}

    current_dt = start_dt
    for step in range(total_intervals):
        hdate = current_dt.strftime("%Y-%m-%d_%H:00:00")
        fname_out = f"{p['prefix_out']}{current_dt.strftime('%Y-%m-%d_%H')}"

        print(f"\n--- Processing Interval: {hdate} ---")

        sfcpres, dpres = None, None
        nlat, nlon, nlev = None, None, None
        startlat, startlon, deltalat, deltalon = None, None, None, None

        with open(fname_out, 'wb') as f_out:
            for spc in species_map:
                field = spc['target']
                src_var = spc['source']
                file_key = spc.get('file', 'prefix_in_1')

                key_index = file_key.split('_')[-1]
                suffix_key = f"suffix_in_{key_index}"
                file_suffix = p.get(suffix_key, ".nc4")
                target_fname = f"{p[file_key]}{current_dt.strftime('%Y%m%d')}{file_suffix}"

                if file_key not in active_handles or active_handles[file_key]['name'] != target_fname:
                    if file_key in active_handles:
                        active_handles[file_key]['nc'].close()

                    if os.path.exists(target_fname):
                        print(f"  > Opening Source {file_key}: {os.path.basename(target_fname)}")
                        active_handles[file_key] = {
                            'nc': Dataset(target_fname, 'r'),
                            'name': target_fname
                        }
                    else:
                        print(f"  ! WARNING: File not found: {target_fname}")
                        if file_key in active_handles: del active_handles[file_key]

                if file_key in active_handles:
                    nc = active_handles[file_key]['nc']

                    if nlat is None:
                        nlat, nlon, nlev = len(nc.dimensions['lat']), len(nc.dimensions['lon']), len(nc.dimensions['lev'])
                        y1d, x1d = nc.variables['lat'][:], nc.variables['lon'][:]
                        startlat, startlon = float(y1d[0]), float(x1d[0])
                        deltalat, deltalon = float(y1d[1]-y1d[0]), float(x1d[1]-x1d[0])

                    file_n_times = len(nc.dimensions['time'])
                    t_idx = (current_dt.hour // p['interval_hr']) if file_n_times > 1 else 0

                    if src_var in nc.variables:
                        print(f"  ! Processing {src_var}")
                        data = nc.variables[src_var][t_idx, :] * spc.get('weight', 1.0)
                        is_3d = (data.ndim == 3)
                        curr_nlev = data.shape[0] if is_3d else 1
                    else:
                        raise KeyError(f"Required MERRA chemistry field {src_var!r} is missing from {target_fname}")
                else:
                    raise FileNotFoundError(f"Required MERRA chemistry source is unavailable: {target_fname}")

                levels = range(curr_nlev) if is_3d else [0]
                for k in levels:
                    kk = (curr_nlev - 1 - k) if is_3d else 0
                    slab = (data[kk, :, :] if is_3d else data).astype('>f4')
                    xlvl = float(k + 1)

                    write_fortran_record(f_out, (IFV, 'i'))
                    write_fortran_record(f_out, (hdate, '24s'), (0.0, 'f'), ("MERRA2", '32s'),
                                         (field, '9s'), (spc['units'], '25s'), (spc['desc'], '46s'),
                                         (xlvl, 'f'), (nlon, 'i'), (nlat, 'i'), (IPROJ, 'i'))
                    write_fortran_record(f_out, (STARTLOC, '8s'), (startlat, 'f'), (startlon, 'f'),
                                         (deltalat, 'f'), (deltalon, 'f'), (EARTH_RADIUS, 'f'))
                    write_fortran_record(f_out, (IS_WIND_EARTH_REL, '?'))
                    write_fortran_record(f_out, (slab.tobytes(), 'b'))

                if field == 'PS': sfcpres = data
                if field == 'DPRES': dpres = data

            # --- 6. CALCULATE AND WRITE 'PRES' (Layer Center Pressure) ---
            # This happens AFTER the species loop finishes for the current interval
            if dpres is not None:
                print(f"  -> Calculating and writing 3D Pressure (PRES)")
                pres_3d = np.zeros((nlev, nlat, nlon), dtype='f4')
                ptop = 1.0  # Pa

                pres_3d[0, :, :] = ptop + 0.5 * dpres[0, :, :]
                for k in range(1, nlev):
                    pres_3d[k, :, :] = pres_3d[k-1, :, :] + 0.5 * (dpres[k-1, :, :] + dpres[k, :, :])

                # Metadata for PRES
                field_pres, units_pres = 'PRES', 'Pa'
                desc_pres = 'Pressure center of layers'
                map_src_pres = "Calculated from DELP"

                for k in range(nlev):
                    kk = (nlev - 1 - k)
                    slab = pres_3d[kk, :, :].astype('>f4')
                    xlvl = float(k + 1)

                    write_fortran_record(f_out, (IFV, 'i'))
                    write_fortran_record(f_out, (hdate, '24s'), (0.0, 'f'), (map_src_pres, '32s'),
                                         (field_pres, '9s'), (units_pres, '25s'), (desc_pres, '46s'),
                                         (xlvl, 'f'), (nlon, 'i'), (nlat, 'i'), (IPROJ, 'i'))
                    write_fortran_record(f_out, (STARTLOC, '8s'), (startlat, 'f'), (startlon, 'f'),
                                         (deltalat, 'f'), (deltalon, 'f'), (EARTH_RADIUS, 'f'))
                    write_fortran_record(f_out, (IS_WIND_EARTH_REL, '?'))
                    write_fortran_record(f_out, (slab.tobytes(), 'b'))

        current_dt += timedelta(hours=p['interval_hr'])

    for h in active_handles.values(): h['nc'].close()

if __name__ == "__main__":
    run_conversion()
