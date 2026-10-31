"""Write MERRA-2 / MERRA2-GMI chemistry fields as WPS intermediate files for init_atmosphere.

Called by ``merra_chem.py`` (PrepareChemIC) through ``run_conversion(config_path)``.  The
configuration (``config/chemistry/merra_processor.yaml``, templated at run time) names the source
files, the output prefix, the time stepping, and the species mapping.  One intermediate file is
written per valid time, ``<prefix_out><YYYY-mm-dd_HH>``, holding every mapped field plus the
layer-centre pressure derived from the pressure-thickness field.

Intermediate-file layout (WPS "version 5" format, big-endian Fortran unformatted records):
for every 2-D slab, a record with the format version, a record with the field metadata,
a record with the lat-lon grid description, a record with the wind-rotation flag, and a record
with the slab itself as 32-bit floats in row-major (lat, lon) order.  3-D fields are written
one slab per model layer, numbered from the surface upward, so the top-of-file layer index 1
is the lowest MERRA-2 layer.

A missing source file or a missing source variable is an error: the workflow must never start a
forecast from silently zeroed chemistry.
"""
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import yaml
from netCDF4 import Dataset

FORMAT_VERSION = 5
PROJECTION_LATLON = 0
START_LOCATION = "SWCORNER"
EARTH_RADIUS_KM = 6367470 * 0.001
MAP_SOURCE = "MERRA2"
PRESSURE_FIELD = ("PRES", "Pa", "Pressure center of layers", "Calculated from DELP")
MODEL_TOP_PA = 1.0


@dataclass
class Grid:
    """Regular lat-lon grid of the source files, as the intermediate header describes it."""
    nlon: int
    nlat: int
    nlev: int
    start_lat: float
    start_lon: float
    delta_lat: float
    delta_lon: float

    @classmethod
    def from_dataset(cls, nc):
        lat = nc.variables["lat"][:]
        lon = nc.variables["lon"][:]
        return cls(nlon=len(nc.dimensions["lon"]), nlat=len(nc.dimensions["lat"]), nlev=len(nc.dimensions["lev"]),
                   start_lat=float(lat[0]), start_lon=float(lon[0]),
                   delta_lat=float(lat[1] - lat[0]), delta_lon=float(lon[1] - lon[0]))


def fixed_string(text, width):
    """Fortran CHARACTER(width): ASCII, right-padded with blanks, truncated if longer."""
    return str(text).ljust(width)[:width].encode("ascii")


def fortran_record(payload):
    """Wrap a byte payload in the 4-byte big-endian length markers of an unformatted record."""
    marker = struct.pack(">i", len(payload))
    return marker + payload + marker


def write_slab(out, hdate, field, units, description, map_source, level, grid, slab):
    """Write one 2-D field (the five records of the version-5 intermediate format)."""
    out.write(fortran_record(struct.pack(">i", FORMAT_VERSION)))
    header = (fixed_string(hdate, 24) + struct.pack(">f", 0.0) + fixed_string(map_source, 32)
              + fixed_string(field, 9) + fixed_string(units, 25) + fixed_string(description, 46)
              + struct.pack(">f", float(level)) + struct.pack(">iii", grid.nlon, grid.nlat, PROJECTION_LATLON))
    out.write(fortran_record(header))
    projection = (fixed_string(START_LOCATION, 8)
                  + struct.pack(">fffff", grid.start_lat, grid.start_lon, grid.delta_lat, grid.delta_lon, EARTH_RADIUS_KM))
    out.write(fortran_record(projection))
    out.write(fortran_record(struct.pack(">i", 0)))          # winds are not grid-relative
    out.write(fortran_record(slab.astype(">f4").tobytes()))


def write_field(out, hdate, field, units, description, map_source, grid, data):
    """Write a 2-D field as level 1, or a 3-D (lev, lat, lon) field surface-up as levels 1..nlev."""
    if data.ndim == 2:
        write_slab(out, hdate, field, units, description, map_source, 1, grid, data)
        return
    nlev = data.shape[0]
    for level in range(1, nlev + 1):
        source_layer = nlev - level           # MERRA-2 stores layers top-down
        write_slab(out, hdate, field, units, description, map_source, level, grid, data[source_layer])


def layer_centre_pressure(delta_p, nlev, grid):
    """Pressure at layer centres from the top of the model down, accumulated layer by layer."""
    pressure = np.zeros((nlev, grid.nlat, grid.nlon), dtype="f4")
    pressure[0] = MODEL_TOP_PA + 0.5 * delta_p[0]
    for k in range(1, nlev):
        pressure[k] = pressure[k - 1] + 0.5 * (delta_p[k - 1] + delta_p[k])
    return pressure


class SourceFiles:
    """Open the source NetCDF files named by the configuration, one handle per file key."""

    def __init__(self, params):
        self.params = params
        self.handles = {}

    def path_for(self, file_key, when):
        suffix = self.params.get("suffix_in_" + file_key.split("_")[-1], ".nc4")
        return f"{self.params[file_key]}{when:%Y%m%d}{suffix}"

    def dataset(self, file_key, when):
        path = self.path_for(file_key, when)
        entry = self.handles.get(file_key)
        if entry is None or entry[0] != path:
            if entry is not None:
                entry[1].close()
            try:
                nc = Dataset(path, "r")
            except OSError as err:
                raise FileNotFoundError(f"Required MERRA chemistry source is unavailable: {path}") from err
            self.handles[file_key] = (path, nc)
        return self.handles[file_key][1]

    def close(self):
        for _, nc in self.handles.values():
            nc.close()
        self.handles = {}


def read_field(nc, variable, when, interval_hr, path):
    """One time slice of a variable; files with a single time use it for every hour."""
    if variable not in nc.variables:
        raise KeyError(f"Required MERRA chemistry field {variable!r} is missing from {path}")
    time_index = when.hour // interval_hr if len(nc.dimensions["time"]) > 1 else 0
    return nc.variables[variable][time_index, :]


def convert_one_time(when, params, species_map, sources):
    """Write the intermediate file for one valid time."""
    hdate = when.strftime("%Y-%m-%d_%H:00:00")
    output_path = f"{params['prefix_out']}{when:%Y-%m-%d_%H}"
    print(f"\n--- Processing Interval: {hdate} ---")
    grid = None
    delta_p = None
    with open(output_path, "wb") as out:
        for entry in species_map:
            file_key = entry.get("file", "prefix_in_1")
            nc = sources.dataset(file_key, when)
            if grid is None:
                grid = Grid.from_dataset(nc)
            data = read_field(nc, entry["source"], when, params["interval_hr"], sources.path_for(file_key, when))
            data = data * entry.get("weight", 1.0)
            print(f"  ! Processing {entry['source']}")
            write_field(out, hdate, entry["target"], entry["units"], entry["desc"], MAP_SOURCE, grid, data)
            if entry["target"] == "DPRES":
                delta_p = data
        if delta_p is not None:
            print("  -> Calculating and writing 3D Pressure (PRES)")
            field, units, description, map_source = PRESSURE_FIELD
            pressure = layer_centre_pressure(delta_p, grid.nlev, grid)
            write_field(out, hdate, field, units, description, map_source, grid, pressure)
    return output_path


def run_conversion(config_path="config.yaml"):
    """Entry point used by merra_chem.py: convert every interval described by the configuration."""
    with open(config_path, "r") as handle:
        config = yaml.safe_load(handle)
    params = config["control_params"]
    species_map = config["species_map"]
    start = datetime(params["start_year"], params["start_month"], params["start_day"], params["start_hour"])
    steps = params["num_days"] * (24 // params["interval_hr"])
    sources = SourceFiles(params)
    try:
        for step in range(steps):
            convert_one_time(start + timedelta(hours=step * params["interval_hr"]), params, species_map, sources)
    finally:
        sources.close()


if __name__ == "__main__":
    import sys
    run_conversion(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
