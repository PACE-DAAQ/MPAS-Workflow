#!/usr/bin/env python3
"""
Copy warm-start cycling state into a cold MPAS IC.

The cold IC supplies refreshed meteorology and prescribed chemistry
backgrounds.  Two groups of state can be carried across the cycle:

  chemistry (always)  prognostic GOCART2G scalars plus persistent HNO3;
  land (--include-land)  prognostic soil and snow state.

Land cycling matters when meteorology is re-initialised from analysis every
cycle: without it the land surface cold-starts from the analysis each time and
soil moisture/temperature never spin up.  Carrying it is the usual practice in
MPAS-JEDI weather cycling.

IMPORTANT SCOPE LIMIT.  Only land fields that exist in the IC file can be
carried, and an init_atmosphere IC contains the NOAH-level fields only
(verified against x1.163842.init.2024-10-14_00.00.00.nc: smois, tslb, sh2o,
snow, snowh, snowc are present).  NOAH-MP's additional prognostics -- the
diag_physics_noahmp *xy fields such as tahxy, tgxy, canliqxy, snicexy,
snliqxy, tsnoxy, zsnsoxy, zwtxy -- are NOT in the IC file and are rebuilt by
lsminit at every cold start.  Carrying those requires adding them to the
init/restart stream, i.e. a Registry/streams change, not a change here.  So
under sf_noahmp this recovers the soil column but not the canopy/snowpack
state.

Static and boundary fields (isltyp, ivgtyp, xland, tmn, vegfra, sst, xice,
seaice, skintemp) are deliberately NOT carried: they belong to the target
cycle's analysis, and sst/xice in particular are refreshed by updateSea.
"""

from netCDF4 import Dataset
import sys

argv = [a for a in sys.argv[1:]]
aux_file = None
if "--aux-source" in argv:
    i = argv.index("--aux-source")
    aux_file = argv[i + 1]
    del argv[i:i + 2]
include_land = False
carry_hno3 = "--reset-hno3" not in argv
if not carry_hno3:
    argv.remove("--reset-hno3")
for flag in ("--include-land", "--land"):
    if flag in argv:
        include_land = True
        argv.remove(flag)

if len(argv) != 2:
    print("Usage: python copy_mpas_vars.py [--include-land] [--reset-hno3] <src_file> <dst_file>")
    sys.exit(1)

src_file, dst_file = argv

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

if not carry_hno3:
    vars_to_copy.remove("persistent_hno3")
    print("HNO3 carryover disabled: retaining the target cold-IC persistent_hno3")

# Prognostic land state, carried only with --include-land. Treated as optional:
# a chemistry-only IC, or a NOAH IC lacking a NOAH-MP field, must not abort the
# cycle, so anything absent is reported and skipped rather than raised.
land_vars = [
    "smois",    # soil moisture (nSoilLevels)
    "tslb",     # soil temperature (nSoilLevels)
    "sh2o",     # liquid soil water (nSoilLevels)
    "snow",     # snow water equivalent
    "snowh",    # snow depth
    "snowc",    # snow cover fraction
]

optional_vars = land_vars if include_land else []

print(f"Opening source: {src_file}")
src = Dataset(src_file, "r")

print(f"Opening destination: {dst_file}")
dst = Dataset(dst_file, "r+")
# JEDI need not serialize non-analysis state; use the matching member's prior
# forecast for HNO3/land, without replacing any analyzed aerosol scalar.
aux = Dataset(aux_file, "r") if aux_file else src
# These precursors are absent from this workflow's StandardAnalysisVariables.
# Their existing member values must survive JEDI's selected-state arithmetic.
nonanalysis_chemistry = ["qso2", "qso2v", "qso4v", "qdms", "qmsa", "qnh3", "qnh4a",
                        "qsoapa", "qsoapbb", "qsoapbg"]
aux_vars = nonanalysis_chemistry + (["persistent_hno3"] if carry_hno3 else []) + optional_vars
aux_chemistry = nonanalysis_chemistry + ["persistent_hno3"]
if aux_file:
    missing_aux = [v for v in aux_vars if v not in aux.variables or v not in dst.variables]
    if missing_aux:
        raise KeyError(f"Requested carryover unavailable in auxiliary source/target: {missing_aux}")

if include_land:
    print(f"Land cycling ENABLED: will also carry {land_vars}")
else:
    print("Land cycling disabled (pass --include-land to carry soil/snow state)")

missing_src = [v for v in vars_to_copy if v not in (aux if v in aux_chemistry else src).variables]
missing_dst = [v for v in vars_to_copy if v not in dst.variables]

# Optional group: never fatal in either direction.
opt_present = [v for v in optional_vars if v in aux.variables and v in dst.variables]
opt_absent = [v for v in optional_vars if v not in opt_present]
if opt_absent:
    print(
        "WARNING copy_mpas_vars: optional land fields not carried (absent from "
        f"source and/or destination): {opt_absent}",
        file=sys.stderr,
    )

# Missing in the destination is fatal: the destination is the freshly built
# init for this cycle, so an absent field means the init is not chemistry
# enabled and there is nowhere to put the cycled state.
if missing_dst:
    src.close()
    dst.close()
    raise KeyError(
        "Cannot transfer GOCART2G cycling state: "
        f"missing in destination={missing_dst}"
    )

# Missing in the source is not fatal. Warm starts from a background produced
# before a field was added to stream_list.atmosphere.dastate legitimately lack
# it. The destination already holds that cycle's own value, interpolated from
# MERRA2 by init_atmosphere, so keeping it is both safe and more appropriate
# for the date than anything the older source could supply.
if missing_src:
    print(
        "WARNING copy_mpas_vars: not present in source, keeping the value "
        f"init_atmosphere produced for this cycle: {missing_src}",
        file=sys.stderr,
    )

print("Copying variables...")

for v in vars_to_copy:
    if v in missing_src:
        print(f"  skipping {v} (absent from source; destination value retained)")
        continue
    print(f"  copying {v} ...")
    dst[v][:] = (aux if v in aux_chemistry else src)[v][:]

opt_copied = 0
for v in opt_present:
    if aux[v].shape != dst[v].shape:
        print(
            f"WARNING copy_mpas_vars: {v} shape {aux[v].shape} -> {dst[v].shape} "
            "differs; skipping rather than writing a mismatched field",
            file=sys.stderr,
        )
        continue
    print(f"  copying {v} (land) ...")
    dst[v][:] = aux[v][:]
    opt_copied += 1

if aux_file:
    aux.close()
src.close()
dst.close()

# opt_copied, not len(opt_present): a land field present in both files but with a
# mismatched shape is deliberately skipped above, and reporting it as copied would
# contradict the warning that was just printed.
# Say PARTIAL out loud when chemistry fields were retained from the destination
# rather than transferred. The counts alone are accurate but easy to skim past,
# and a partial chemistry transfer otherwise reads in the log exactly like a
# complete one -- cycling silently carrying less state than intended.
chem_copied = len(vars_to_copy) - len(missing_src)
if missing_src:
    print(f"Done. PARTIAL: {chem_copied} of {len(vars_to_copy)} chemistry variables copied "
          f"({len(missing_src)} retained from the destination), {opt_copied} land.")
else:
    print(f"Done. {chem_copied} chemistry + {opt_copied} land variables copied.")

