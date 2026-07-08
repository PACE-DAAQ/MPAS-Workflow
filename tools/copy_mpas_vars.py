#!/usr/bin/env python3
"""
Copy selected chemistry variables from an MPAS history file into
an MPAS init/emissions file. Works with CDF5 files.
"""

from netCDF4 import Dataset
import sys

if len(sys.argv) != 3:
    print("Usage: python copy_mpas_vars.py <src_file> <dst_file>")
    sys.exit(1)

src_file = sys.argv[1]
dst_file = sys.argv[2]

# List of variables to copy
vars_to_copy = [
    "qbcphobic", "qbcphilic",
    "qbrphobic", "qbrphilic",
    "qocphobic", "qocphilic",
    "qdust1", "qdust2", "qdust3", "qdust4", "qdust5",
    "qni1", "qni2", "qni3",
    "qso2", "qso2v",
    "qso4", "qso4v",
    "qseas1", "qseas2", "qseas3", "qseas4", "qseas5",
    "qdms", "qnh3", "qnh4a",
    "qsoapa", "qsoapbb", "qsoapbg"
]

print(f"Opening source: {src_file}")
src = Dataset(src_file, "r")

print(f"Opening destination: {dst_file}")
dst = Dataset(dst_file, "r+")

print("Copying variables...")

for v in vars_to_copy:
    print(f"  copying {v} ...")
    dst[v][:] = src[v][:]     # fastest CDF5-safe method

src.close()
dst.close()

print("Done. All variables copied successfully.")

