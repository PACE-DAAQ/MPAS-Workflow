#!/usr/bin/env python3
"""
Copy the warm-start GOCART2G chemistry state into a cold MPAS IC.

The cold IC supplies refreshed meteorology and prescribed chemistry
backgrounds.  The variables below are the prognostic/persistent chemistry
state that must survive the DA cycle.
"""

from netCDF4 import Dataset
import sys

if len(sys.argv) != 3:
    print("Usage: python copy_mpas_vars.py <src_file> <dst_file>")
    sys.exit(1)

src_file = sys.argv[1]
dst_file = sys.argv[2]

# Prognostic scalar chemistry plus persistent HNO3.
# Do NOT copy background_hno3 here: it is the prescribed relaxation target
# supplied by the target-time cold chemistry IC.
vars_to_copy = [
    "qbcphobic", "qbcphilic",
    "qbrphobic", "qbrphilic",
    "qocphobic", "qocphilic",
    "qdust1", "qdust2", "qdust3", "qdust4", "qdust5",
    "qni1", "qni2", "qni3",
    "qso2", "qso2v",
    "qso4", "qso4v",
    "qseas1", "qseas2", "qseas3", "qseas4", "qseas5",
    "qdms", "qmsa",
    "qnh3", "qnh4a",
    "qsoapa", "qsoapbb", "qsoapbg",
    "persistent_hno3",
]

print(f"Opening source: {src_file}")
src = Dataset(src_file, "r")

print(f"Opening destination: {dst_file}")
dst = Dataset(dst_file, "r+")

missing_src = [v for v in vars_to_copy if v not in src.variables]
missing_dst = [v for v in vars_to_copy if v not in dst.variables]
if missing_src or missing_dst:
    src.close()
    dst.close()
    raise KeyError(
        "Cannot transfer complete GOCART2G cycling state: "
        f"missing in source={missing_src}; missing in destination={missing_dst}"
    )

print("Copying variables...")

for v in vars_to_copy:
    print(f"  copying {v} ...")
    dst[v][:] = src[v][:]     # fastest CDF5-safe method

src.close()
dst.close()

print("Done. All variables copied successfully.")

