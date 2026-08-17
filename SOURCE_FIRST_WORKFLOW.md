# Source-first MPAS-GOCART2G inputs (v1.12)

v1.12 extends the mesh-native emissions work so the workflow can begin from
original/raw resources whenever the project's documented source is known.  It
does **not** silently replace a missing source with a scientifically different
dataset.

## End-to-end model-input path

The MPAS-GOCART2G instructions describe four inputs for a new case: meteorology,
MERRA chemistry initial concentrations, emissions, and prescribed oxidants.
v1.4 maps those into MPAS-Workflow as:

```
raw/original resource
       |
       +-- emissions -------------------------- PrepareEmissions
       |      CAMS / CEDS / FINN / GFAS / QFED       |
       |                                             +--> CDF-5 MPAS emission files
       |
       +-- MERRA aerosol + OVP chemistry ------ PrepareChemIC
       |                                             |
       |                                             +--> MERRA2:YYYY-MM-DD_HH intermediates
       |
       +-- prescribed GMI backgrounds -------- direct validated source directory
                                                     |
                                                     v
                                               init_atmosphere
                                                     |
                                                     v
                                     MPAS state including GOCART2G chemistry
                                                     |
                                                     v
                                                  Forecast
```

`initic.chemistry mode: off` remains the default, so old experiments are not
changed.  `workflow` mode adds `PrepareChemIC => ExternalAnalysisToMPAS` and, if
emissions are also workflow-native, `PrepareEmissions => ExternalAnalysisToMPAS`.
The chemistry-enabled init stream reads the same anthropogenic/biogenic/biomass
burning products selected for the model experiment.

## Table-1 aerosol-emission ensemble

`model.member variants` can reproduce the nine combinations in the supplied
ensemble table:

```yaml
model:
  member variants: [cntl, pert01, pert02, pert03, pert04, pert05, pert06, pert07, pert08]
```

| member | anthropogenic | biomass burning | biogenic |
|---:|---|---|---|
| 1 | CAMS global | FINN | CAMS |
| 2 | CAMS global | GFAS | CAMS |
| 3 | CAMS global | QFED | CAMS |
| 4 | CEDS global | FINN | CAMS |
| 5 | CEDS global | GFAS | CAMS |
| 6 | CEDS global | QFED | CAMS |
| 7 | CAMS global+regional | FINN | CAMS |
| 8 | CAMS global+regional | GFAS | CAMS |
| 9 | CAMS global+regional | QFED | CAMS |

In v1.12 **NH3 follows the anthropogenic inventory** for CAMS-vs-CEDS.  Therefore
members 4-6 read `CEDS_Glb_<year>_MPAS...NH3.nc`, including all sector-resolved
`nh3_anth_*` variables expected by GOCART2G.  CAMS-mix currently retains CAMS
global NH3 because no separate regional-mix NH3 source was supplied; this is
explicit rather than silently fabricating a mixed NH3 product.  Anthropogenic
ISO/MNT also remain CAMS because the present CEDS ensemble definition does not
supply compatible speciated VOC products.

## FINN directly from GDEX on Derecho

The preferred historical FINNv2.5 MODIS+VIIRS source is configured as the NCAR
HPC holding itself:

```
/gdex/data/d312009/<year>_eachfire_modisviirs/
```

No bulk copy is required.  v1.12 supports both the 2012-2021 annual MOZART text
containers and the 2022-2023 monthly FINNv2.5.1 containers.  Huge `.txt.gz`
containers are streamed in chunks; only requested days are extracted into a
small cache before KD-tree mapping to MPAS.  A second source candidate can be
configured for 2024+ NRT data.

The missing-day policy from v1.3 is retained: short gaps may be interpolated,
while consecutive gaps use an explicit fallback inventory (or fail).  Fire-event
emissions are not blindly interpolated across long gaps.

## CEDS/GFAS/QFED/CAMS original inputs

The generic regular-grid adapter accepts a priority-ordered `source.files` list.
On Derecho, put an authoritative GDEX/campaign path first when one exists and a
user cache second.  We only hard-code a GDEX preset where the dataset has been
verified (FINN d312009).  The current project instructions identify CAMS through
ECCAD; they do not identify authoritative GDEX collections for CEDS/GFAS/QFED,
so v1.12 leaves those paths explicit in YAML rather than guessing.

## MERRA chemistry initial conditions

`config/chemistry/merra2_source.example.yaml` drives the existing
`init_MPAS-GOCART2G/run_processing.py` directly from raw files.  The source
recipe follows the project instructions:

- aerosol collection: MERRA2 `inst3_3d_aer_Nv`;
- gas/precursor collection: `MERRA2_GMI.inst0_3d_ovp_Nv`;
- the current project policy uses the archived **2019 monthly OVP** for later
  cases because post-2019 OVP is unavailable/incomplete; same-year aerosol
  chemistry still uses the archived 6-hour MERRA2-GMI files;
- missing required fields are **fatal by default**, rather than being silently
  written as zero by `run_processing.py`;
- provenance records the actual source file/date and any scaling.

Raw-source chemistry sensitivity is also explicit and separate from emission
scaling:

```yaml
scaling:
  HNO3: 1.0
  NH3:  1.0
  NI001: 1.0
  NI002: 1.0
  NI003: 1.0
```

This is the correct place for an HNO3 source sensitivity because HNO3 is not a
forecast emissions stream.

## Prescribed backgrounds and optics

The project instructions currently identify pre-calculated GMI climatological
backgrounds and the Chin et al. optics tables in ACOM campaign storage.  The
workflow can point `build.gocart background lut directory` and
`build.gocart optics directory` directly at those authoritative holdings,
without making another project-local copy.  The documentation does not provide
a raw-GMI-to-BACKGROUND regeneration recipe, so v1.4 deliberately does not
invent one.  A WACCM-based background can be added later behind the same source
interface once its production recipe is defined.

## CDF-5 boundary

Raw inputs may be NetCDF4/HDF5 because Python preprocessing reads them.  Every
new NetCDF file that MPAS opens directly is written as CDF-5
(`NETCDF3_64BIT_DATA`) for SMIOL/PnetCDF compatibility.  SCRIP and ESMF weight
files remain preprocessing-only artifacts.

## Fully raw vs transitional staging

`emissions.seed prebuilt` is an explicit transition switch.  The default `true`
keeps the existing `EmissionDir`/`PRMAreaDir` as a fallback and then overwrites
inventories produced from original sources.  Set it to `false` when every file
required by the selected ensemble members is produced by the source-first
processors.  For the complete 9-member table, CAMS-MIX is currently the one
remaining prebuilt family because the global+regional raw blend recipe was not
provided; members 1-6 can be made fully source-first once CAMS/CEDS/FINN/GFAS/
QFED source configurations are complete.

The chemistry cold-start produced by `ExternalAnalysisToMPAS` is shared across
ensemble members.  Its emission fields use the scenario-wide stream variant
(control by default); the Table-1 member-specific emission perturbations are
applied by `Forecast.csh` through `member variants`.  This avoids generating
nine redundant chemistry initial files merely to perturb forecast emissions.

### PRM source independence

PRM source is independent of biomass inventory.  Keep `prm source: finn` for
FINN/GFAS/QFED ensemble members unless a separately validated fire-size
product is supplied.  Daily GFAS/QFED processing produces biomass emissions;
FINN supplies the required daily fire-size average.
