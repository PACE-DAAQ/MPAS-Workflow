#!/usr/bin/env python3
"""
Copy warm-start cycling state into a cold MPAS IC.

The cold IC supplies refreshed meteorology and prescribed chemistry
backgrounds.  Two groups of state can be carried across the cycle:

  chemistry (always)  prognostic GOCART2G scalars plus persistent HNO3;
  land (--include-land)  prognostic soil and snow state;
  hydrometeors (--include-hydrometeors) cloud, rain, ice, snow and graupel.

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

Non-negativity (always on).  After the carry, every water-vapour,
hydrometeor and chemistry/aerosol scalar in the destination IC is clipped at
zero.  Interpolated external analyses and JEDI analyses both produce small
negative mixing ratios, and a negative scalar entering the model physics can
crash atmosphere_model (SIGSEGV in the first few time steps).  Clipped counts
and the most negative value per variable are printed so the clipping is never
silent.

Static and boundary fields (isltyp, ivgtyp, xland, tmn, vegfra, sst, xice,
seaice, skintemp) are deliberately NOT carried: they belong to the target
cycle's analysis, and sst/xice in particular are refreshed by updateSea.
"""

from netCDF4 import Dataset
import numpy as np
import sys

argv = [a for a in sys.argv[1:]]
aux_file = None
if "--aux-source" in argv:
    i = argv.index("--aux-source")
    aux_file = argv[i + 1]
    del argv[i:i + 2]
include_land = False
include_hydrometeors = False
carry_hno3 = "--reset-hno3" not in argv
if not carry_hno3:
    argv.remove("--reset-hno3")
for flag in ("--include-land", "--land"):
    if flag in argv:
        include_land = True
        argv.remove(flag)
if "--include-hydrometeors" in argv:
    include_hydrometeors = True
    argv.remove("--include-hydrometeors")

if len(argv) != 2:
    print("Usage: python copy_mpas_vars.py [--include-land] [--include-hydrometeors] "
          "[--reset-hno3] <src_file> <dst_file>")
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

# Thompson microphysics prognostic condensates. Water vapour is refreshed with
# the meteorological analysis; these five hydrometeor species are carried to
# avoid cold-starting cloud condensate at every six-hour cycle.
hydrometeor_vars = ["qc", "qr", "qi", "qs", "qg"]

optional_vars = []
if include_land:
    optional_vars += land_vars
if include_hydrometeors:
    optional_vars += hydrometeor_vars

# Scalars that must never be negative: water vapour, the hydrometeors, the
# microphysics number concentrations, all prognostic chemistry/aerosol tracers
# (persistent_hno3 is a tracer too) and the prescribed chemistry backgrounds.
# Fields absent from the destination are skipped.
water_vapour_vars = ["qv"]
number_concentration_vars = ["ni", "nr", "nc"]
background_chemistry_vars = ["background_h2o2", "background_oh", "background_no3", "background_hno3"]
nonnegative_vars = list(dict.fromkeys(
    water_vapour_vars + hydrometeor_vars + number_concentration_vars
    + vars_to_copy + background_chemistry_vars))


def clip_negative_scalars(dataset, names):
    """Clip the named variables at zero in place; return {name: (count, most_negative)}."""
    clipped = {}
    for name in names:
        if name not in dataset.variables:
            continue
        variable = dataset.variables[name]
        values = variable[:]
        negative = values < 0
        count = int(np.ma.sum(negative))
        if count == 0:
            continue
        clipped[name] = (count, float(np.ma.min(values)))
        variable[:] = np.ma.maximum(values, 0)
    return clipped


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
# Only the required chemistry carryover is fatal here; the optional land group
# is handled by opt_present/opt_absent below and must never abort the cycle.
aux_required = nonanalysis_chemistry + (["persistent_hno3"] if carry_hno3 else [])
aux_chemistry = nonanalysis_chemistry + ["persistent_hno3"]
if aux_file:
    missing_aux = [v for v in aux_required if v not in aux.variables or v not in dst.variables]
    if missing_aux:
        raise KeyError(f"Requested carryover unavailable in auxiliary source/target: {missing_aux}")

if include_land:
    print(f"Land cycling ENABLED: will also carry {land_vars}")
else:
    print("Land cycling disabled (pass --include-land to carry soil/snow state)")
if include_hydrometeors:
    print(f"Hydrometeor cycling ENABLED: will also carry {hydrometeor_vars}")
    # The staged GFS init files contain qc/qr but not always qi/qs/qg. These
    # fields are registered MPAS scalars and are present in the prior forecast,
    # so add missing variables to the cold IC before copying them. Every species
    # must exist in the source: carrying only some of them would silently defeat
    # "carry all hydrometeors", so a gap is fatal and is checked before the
    # destination is modified.
    missing_hydrometeors = [name for name in hydrometeor_vars if name not in aux.variables]
    if missing_hydrometeors:
        raise KeyError(
            "Requested hydrometeor carryover unavailable in "
            f"{aux_file or src_file}: {missing_hydrometeors}"
        )
    for name in hydrometeor_vars:
        if name in dst.variables:
            continue
        source_var = aux[name]
        missing_dims = [dim for dim in source_var.dimensions if dim not in dst.dimensions]
        if missing_dims:
            raise KeyError(
                f"Cannot add hydrometeor {name}: destination lacks dimensions {missing_dims}"
            )
        fill_value = getattr(source_var, "_FillValue", None)
        kwargs = {"fill_value": fill_value} if fill_value is not None else {}
        target_var = dst.createVariable(name, source_var.datatype, source_var.dimensions, **kwargs)
        target_var.setncatts({
            attr: source_var.getncattr(attr)
            for attr in source_var.ncattrs()
            if attr != "_FillValue"
        })
        print(f"  added missing hydrometeor variable {name} to destination IC")
else:
    print("Hydrometeor cycling disabled (pass --include-hydrometeors to carry condensate)")

missing_src = [v for v in vars_to_copy if v not in (aux if v in aux_chemistry else src).variables]
missing_dst = [v for v in vars_to_copy if v not in dst.variables]

# Optional group: land fields remain non-fatal. Requested hydrometeors were
# verified and, where needed, created above, so only land fields can be absent.
opt_present = [v for v in optional_vars if v in aux.variables and v in dst.variables]
opt_absent = [v for v in optional_vars if v not in opt_present]
if opt_absent:
    print(
        "WARNING copy_mpas_vars: optional land/hydrometeor fields not carried (absent from "
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
        if v in hydrometeor_vars:
            raise ValueError(
                f"Requested hydrometeor {v} shape {aux[v].shape} -> {dst[v].shape} differs"
            )
        print(
            f"WARNING copy_mpas_vars: {v} shape {aux[v].shape} -> {dst[v].shape} "
            "differs; skipping rather than writing a mismatched field",
            file=sys.stderr,
        )
        continue
    print(f"  copying {v} (land) ...")
    dst[v][:] = aux[v][:]
    opt_copied += 1

clipped_scalars = clip_negative_scalars(dst, nonnegative_vars)
if clipped_scalars:
    total_clipped = sum(count for count, _ in clipped_scalars.values())
    print(f"Non-negativity clip: set {total_clipped} negative values to zero in "
          f"{len(clipped_scalars)} variables:")
    for name, (count, most_negative) in clipped_scalars.items():
        print(f"  clipped {name}: {count} values, most negative {most_negative:.4g}")
else:
    print("Non-negativity clip: no negative scalar values found")
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
          f"({len(missing_src)} retained from the destination), {opt_copied} optional state fields.")
else:
    print(f"Done. {chem_copied} chemistry + {opt_copied} optional state fields copied.")
