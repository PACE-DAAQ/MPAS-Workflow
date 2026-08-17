# Mesh-native GOCART2G emissions preprocessing — v1.12

## Design

```text
raw inventory                authoritative MPAS mesh
-------------                -----------------------
CAMS/CEDS/GFAS/QFED          centers / areas / polygons / topology
      |                                  |
      +--> source GRIDSPEC                +--> destination SCRIP
                      \                  /
                       ESMF conservative weights
                              (cached)
                                |
                       sparse S,row,col apply
                                |
FINN points --> spherical KD-tree/polygon lookup
                                |
                         time-gap resolver
                                |
                    optional explicit scaling
                                |
                   MPAS CDF-5 forecast inputs
```

Original ESMF emissions-regridding methodology/utility lineage is credited to
**Duseong Jo (2021)**.  The MPAS-Workflow orchestration here is a new,
mesh-native implementation.

## Inventory adapters

* CAMS anthropogenic: sector-aware; AWB exclusion subtracts AWB from `sum` and
  zeros the individual AWB field. Species/field/sector scaling is supported.
* CAMS biogenic: conservative regular-grid remap with optional scaling.
* CEDS/GFAS/QFED: one config-driven regular-grid adapter with unit conversion,
  arbitrary source-variable combinations, missing-time interpolation, scaling,
  and streaming CDF-5 output.
* FINN: point-source adapter using native MPAS geometry, consecutive-day
  fallback, optional forced fallback ranges, robust/fixed fallback scaling,
  the annual-hourly compatibility file, and daily PRM fire statistics. Only
  fire-size average is currently mandatory; optional AREA std/FRP fields are
  produced when possible and warn rather than fail when absent.

## Current FC/DA compatibility

`CONFIG_COMPATIBILITY.md` records the check against the supplied configuration
archives and latest model/workflow code. DA does not read emissions directly.
The latest PRM interface exposes four lowbc stream slots, but the PRM author
clarified that only `prm_lowbc_area_avg` is mandatory. A standalone validator
checks the final resolved streams file against staged inputs while downgrading
the other three PRM fields to warnings.

## Missing-data policy

For smooth gridded inventories, interpolation remains configurable and is
bounded by a maximum bracketing gap.  For FINN, fire-event intermittency is
handled more conservatively:

1. exact FINNv2 record when available;
2. short interpolation only when explicitly allowed;
3. long/consecutive gap -> explicit fallback inventory;
4. optional forced fallback range for a known questionable interval;
5. fatal error if neither source is defensible.

Fallback amplitude scaling defaults to `none`. `robust_median_ratio` is
available as a diagnostic/sensitivity option and uses median domain-integrated
ratios plus clipping rather than an event-sensitive mean.

## Emissions scaling versus HNO3

Scaling is a controlled sensitivity mechanism, not a hidden tuning constant.
It is recorded in output provenance and defaults to 1.0.  Current forecast
emissions include NH3 but **not HNO3**.  HNO3 is part of chemical initialization
and nitrate chemistry/nudging, so any HNO3 scale factor should live in that
pathway in a later code change.

## SMIOL I/O

All files written for direct MPAS input use CDF-5
`NETCDF3_64BIT_DATA`. SCRIP/GRIDSPEC/weight files are preprocessing-only and are
not opened by MPAS.
