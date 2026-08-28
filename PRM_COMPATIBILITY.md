# MPAS-GOCART2G plume-rise (PRM) compatibility — v1.12

This note is based on the latest supplied `gocartMPAS-main.zip`, the two
uploaded 2024 FINNv2.5.1 sample files, and the PRM author's clarification in
the MPAS-Workflow issues.

## Current input severity

The latest Registry exposes four low-boundary-condition fields:

| stream | MPAS field | units | workflow severity |
|---|---|---:|---|
| `prm_lowbc_area_avg` | `firesize_biob_modis_avg` | m2 | **required** |
| `prm_lowbc_area_std` | `firesize_biob_modis_std` | m2 | optional / warning |
| `prm_lowbc_frp_avg` | `frp_biob_modis_avg` | MW | optional / warning |
| `prm_lowbc_frp_std` | `frp_biob_modis_std` | MW | optional / warning |

The PRM author explicitly clarified that only `prm_lowbc_area_avg` is currently
required.  The other three messages should be warnings rather than errors
because their absence does not prevent model execution.

The current Fortran init/update routines still call the four stream reads, so
v1.12 keeps all four stream definitions in the MPAS templates.  The difference
is validation severity: only a missing fire-size average aborts the workflow.
Workflow-generated FINN PRM files populate all four fields whenever the source
information is available; optional fields that cannot be populated are written
as zero with a provenance warning.  Existing external PRM files containing only
`firesize_biob_modis_avg` are accepted by `validate_streams` with warnings.

## FINN scalar definition

The workflow uses the base names `AREA: firesize_biob_modis` and
`FRP: frp_biob_modis`.  Scalar aggregation is
per MPAS cell over individual accepted FINN records:

- mean = sum(x) / N
- standard deviation = sqrt(sum(x^2)/N - mean^2)

Thus the standard deviation is the **population** standard deviation (`ddof=0`),
not a sample standard deviation and not a FIREID-grouped statistic.  v1.12
matches this definition.

## Check with the uploaded 2024 FINN samples

The supplied files

- `FINNv2.5.1_modvrs_nrt_MOZART_20241101.txt.gz`
- `FINNv2.5.1_modvrs_nrt_base_FRP_20241101.txt.gz`

are valid comma-separated files and each contains 40,905 fire records for day
306.  For all shared fields checked (including latitude/longitude, AREA,
BC/OC/CO/NH3/SO2), the records are identical row-for-row.  The base-FRP file
adds FRP.

This has an important consequence: **the MOZART emissions file itself can
supply the currently required PRM fire-size average because it contains AREA.**
A dedicated `base_FRP` file is therefore optional for current model execution;
it is preferred when available because it supplies FRP and is the clearest
source for the optional fire-property diagnostics.

v1.12 detects the FINN delimiter from file content.  This corrects an earlier
filename-based workaround that would have misread the uploaded 2024 base-FRP
sample as whitespace-delimited.

## Missing/consecutive FINN days

PRM fire-size resolution now follows this hierarchy for each day:

1. dedicated FINN `base_FRP`/PRM source, if available;
2. primary FINN MOZART each-fire source (AREA supplies required fire size);
3. short interpolation between neighboring actual AREA records, within the
   configured gap limit;
4. configured FINNv1/fallback source for long/consecutive gaps.

Only failure to obtain/interpolate **AREA average** is fatal.  AREA std and FRP
avg/std are warning-only.  When FRP is absent from a source, v1.12 writes zero
FRP avg/std in files it creates and records a warning in provenance.

`config_do_FRP=true` is retained in provenance because it changes the plume
heat-flux calculation.  Missing FRP still does not abort preprocessing, in
accordance with the PRM author's stated input contract, but the workflow emits
a stronger scientific warning that zero/default FRP should not be interpreted
as observed FRP.

## PRM initialization

The latest init Registry provides `&preproc_plumerisemodel` /
`config_init_bburnPRM`.  The workflow's GOCART2G init template maps this to the
same scenario `do bburn prm` flag used by the forecast, so a cold start can
initialize the PRM lowbc state consistently.

## Biomass emissions redistributed by PRM

The latest plume-rise implementation vertically redistributes BC, BrC, OC,
SO2, NH3, and the biomass SOA precursor.  BrC is derived internally from BC/OC,
and the SOA precursor uses the biomass CO pathway.  Consequently the essential
external biomass inventory fields supporting this redistribution are BC, OC,
SO2, NH3, and CO.  FINN/GFAS/QFED processing in this package provides them.

FINN fire properties remain the common PRM driver when biomass mass emissions
are perturbed among FINN, GFAS, and QFED; this isolates inventory
magnitude/composition perturbations from plume-rise geometry.

## GFAS/QFED members

PRM forcing is intentionally decoupled from the biomass-emission inventory.
For the current GOCART2G PRM, only `firesize_biob_modis_avg` is mandatory.
QFED does not provide a compatible fire-size variable.  GFAS provides FRP and
its own plume-height diagnostics, but these are gridded products and are not
equivalent to the FINN per-fire mean AREA consumed by this PRM.  Therefore the
recommended ensemble design is:

- FINN member: FINN emissions + FINN PRM fire size
- GFAS member: GFAS emissions + FINN PRM fire size
- QFED member: QFED emissions + FINN PRM fire size

This holds plume-rise geometry fixed while perturbing biomass emission
magnitude/composition.  `emissions: prm source: finn` enforces this even when
`prepare` does not otherwise request FINN biomass emissions.
