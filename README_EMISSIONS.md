# MPAS-Workflow emissions v1.4 — quick start

`emissions.mode` still defaults to `prebuilt`, so existing experiments are
unchanged.  `workflow` mode stages the production emissions/PRM inputs and can
replace selected inventories with mesh-native products.

```yaml
emissions:
  mode: workflow
  prepare: [mesh, cams-anth, cams-biog, finn]
  cams anth config: /path/to/cams_anth.yaml
  cams biog config: /path/to/cams_biog.yaml
  finn config: /path/to/finn.yaml
  finn reject outside: true
```

CEDS/GFAS/QFED use the shared regular-grid adapter:

```yaml
emissions:
  mode: workflow
  prepare: [mesh, ceds, gfas, qfed]
  ceds config: /path/to/ceds.yaml
  gfas config: /path/to/gfas.yaml
  qfed config: /path/to/qfed.yaml
```

## Confirmed against the current DA/FC configs

See `CONFIG_COMPATIBILITY.md`.  The important operational points are:

* Emissions are **forecast inputs only** in the supplied configuration; DA has
  no direct emissions streams.
* Table-1 members 1-3 use CAMS anthropogenic emissions; members 4-6 now use CEDS for **NH3 as well as BC/OC/SO2/CO**.
* Control forecast streams require CAMS anthropogenic/biogenic emissions and
  the 7-species FINN hourly biomass file. When PRM is enabled, only
  `firesize_biob_modis_avg` is a hard PRM input; AREA std and FRP avg/std are
  optional. Workflow-generated FINN PRM files populate all four when possible.
* `config_gocart2G_biobemis_interval` is hourly; PRM fire properties are daily.
* v1.8 generates both FINN files and stages the four-field PRM file through the legacy `PRMAreaDir` variable in workflow mode.
* All generated MPAS-facing files are CDF-5 (`NETCDF3_64BIT_DATA`), never HDF5.

After a forecast `streams.atmosphere` has been fully resolved, validate the
actual I/O contract with:

```bash
PYTHONPATH=tools python -m mpas_emissions.validate_streams \
  streams.atmosphere --directory .
```

The validator checks every referenced emissions/PRM file and classic-family
NetCDF container. Missing emissions variables and PRM fire-size average are
fatal; missing PRM AREA std / FRP avg / FRP std are warnings.

## Consecutive missing FINN days

FINN point emissions are episodic.  v1.4 therefore separates short gaps from
long/consecutive gaps:

```yaml
missing_days:
  max linear missing days: 1
  fallback:
    emis_dir: /path/to/FINNv1/{year}/
    emis_file_pattern: FINNv1_MOZART_{year}{month}*.txt
    scale method: none
```

* exact primary FINNv2 day -> use FINNv2;
* short missing span <= `max linear missing days` -> linear interpolation on the
  MPAS mesh;
* longer missing span -> use the fallback day directly;
* no valid primary/fallback -> fail rather than silently fabricate emissions.

A known period can be forced to the fallback even if edge days exist in the
primary source:

```yaml
    force date ranges:
      - ['2024-11-04', '2024-11-10']
```

This supports the project team's proposed direct FINNv1 replacement test.  The
fallback default is deliberately **unscaled**.  An optional
`robust_median_ratio` mode uses median domain-integrated overlap ratios with
clipping; it is safer than an event-sensitive mean ratio but should still be
validated against observations.

## Missing times in CEDS/GFAS/QFED

The regular-grid engine supports `linear`, `nearest`, or `hold` interpolation
with a maximum permitted gap and no extrapolation by default.  It remaps only
the two bracketing records, then interpolates on the MPAS mesh, and streams the
result to CDF-5.

Example:

```yaml
time:
  target frequency: hourly
  target step hours: 1
  missing: linear
  max interpolation gap hours: 6
  allow extrapolation: false
```

For an already-regridded MPAS file, use `mpas_emissions.fill_times` without
repeating the horizontal regrid.

## Optional emissions scaling

Every raw-inventory adapter now accepts explicit multiplicative sensitivity
factors; default 1.0 means no change.

```yaml
scaling:
  default: 1.0
  species:
    nh3: 0.7
  fields:
    nh3_anth_sum: 1.0
```

FINN accepts source aliases such as `NH3`; CAMS accepts long/short species names
and optional sector factors; CEDS/GFAS/QFED recognize the MPAS field prefix
(e.g. `nh3_biob_modis -> nh3`).  Factors and missing-day provenance are written
to output metadata/JSON sidecars.

**HNO3 is not an emissions stream in the current forecast configuration.**
Scaling here applies to NH3 and other emitted species.  HNO3 sensitivity is implemented separately in the raw MERRA/GMI chemistry-initialization config (or can later be tested in the HNO3 nudging itself), not in the emissions adapter.

## Variable-resolution meshes

No nominal resolution is assumed.  MPAS polygons/areas come from the active
mesh; SCRIP and conservative weights are generated/cached by geometry
fingerprints.  FINN uses the spherical KD-tree plus optional exact polygon
containment for regional boundaries.

## Runtime requirements

Normal weight reuse/application needs `numpy`, `scipy`, `pandas`, `yaml`, and a
NetCDF-C capable Python `netCDF4` package with CDF-5 support.  New weights also
need ESMPy/ESMF or `ESMF_RegridWeightGen`.


## Source-first model initialization (v1.4)

See `SOURCE_FIRST_WORKFLOW.md`. Historical FINNv2.5/2.5.1 can be read directly
from the verified GDEX d312009 NCAR-HPC paths. `initic.chemistry mode: workflow`
adds a `PrepareChemIC` task that starts from raw MERRA aerosol/OVP files, then
runs chemistry-enabled `init_atmosphere` with the same emission inventory
selection used by the model. Prescribed GMI backgrounds can be linked directly
from the documented ACOM campaign holding.

HNO3 chemistry-source scaling is configured in
`config/chemistry/merra2_source.example.yaml`; it is intentionally separate from
NH3 emission scaling.


## nmmm0081 site archive defaults (v1.6)

The project archive supplied in `list.Data.txt` is treated as the preferred
Derecho resource root:

`/glade/campaign/ncar/nmmm0081/Data`

See `PROJECT_DATA_PATHS.md` for the audited mapping. Build defaults use the
local `MPAS-Workflow/gocart2g/emission/all`, `backgrounds`, `optics`, and
`prm` directories. CEDS/GFAS/QFED source-first configs prefer their local
`EMIS/*/orig` directories. FINN raw remains GDEX/ACOM because the local
`EMIS/FINN` entry is a symlink; raw CAMS remains explicit because no
`EMIS/CAMS/orig` directory appeared in the supplied listing.

The archived 2024 MERRA aerosol chemistry is stored as 6-hour single-time
files. `tools.mpas_inputs.merra_chem` now supports `processing mode:
time-sliced` and uses these local files without treating one record as a
daily container.

## v1.12: latest `lowbc` plume-rise interface

The latest `gocartMPAS-main` source uses
`firesize_biob_modis_avg/std` and `frp_biob_modis_avg/std` through
`prm_lowbc_area_avg/std` and `prm_lowbc_frp_avg/std`.  The PRM author clarified
that **only `prm_lowbc_area_avg` is currently required**; the other three are
optional and should produce warnings rather than workflow errors.

v1.12 therefore keeps all four stream slots (and writes all four fields when
source data permit) but validates only fire-size average as mandatory.  The
uploaded 2024 FINN samples also show that MOZART and base_FRP contain identical
AREA records, so MOZART itself can supply the required fire-size average when a
dedicated base_FRP file is unavailable.  FINNv1 can similarly supply AREA for a
long/consecutive FINNv2.5 gap. Missing optional FRP is zero-filled in files the
workflow generates and recorded as a warning/provenance item. The same PRM
enable flag is propagated into GOCART2G `init_atmosphere`.

## Biomass inventory versus PRM source

The biomass-emission inventory and the plume-rise fire-property source are
independent.  `biob emissions` may be `finn`, `gfas`, or `qfed`, while
`emissions: prm source: finn` (the default) supplies the required
`firesize_biob_modis_avg` from FINN for every member.  This is intentional:
QFED provides gridded emission fluxes but no FINN-compatible fire-size field,
and GFAS provides gridded FRP/injection-height information but not the
per-fire mean area in m2 expected by the current GOCART2G PRM interface.

Thus a GFAS/QFED member uses GFAS/QFED mass emissions with FINN fire size.  The
three optional PRM fields are generated when FINN provides them and otherwise
remain warning-only under the PRM-author contract.  Setting `prm source:
prebuilt` retains an existing validated PRM file instead.


### Mesh-aware output names

Workflow-native emission filenames use the active MPAS grid label `x<meshRatio>.<nCells>` (for example `x1.163842` or `x6.828394`).  `PrepareEmissions` exports this label to all inventory processors, and `SetStreamsVariant.csh` resolves the same label at forecast runtime.
