# MPAS-Workflow issue alignment — emissions / chemistry-IC refactor v1.12

Reviewed against the open issues in `PACE-DAAQ/MPAS-Workflow` on 17 Aug 2026.
This refactor intentionally addresses only issues in the emissions / GOCART
input-preparation scope; unrelated DA/observation/SST/verification work is not
modified.

| issue | topic | v1.12 relationship |
|---:|---|---|
| #2 | Initial Condition for gocartMPAS | **directly addressed**: `PrepareChemIC`, chemistry-enabled init namelist/streams, backgrounds/emissions staging, bundled MERRA intermediate converter, archived same-year aerosol + 2019 monthly OVP policy |
| #3 | Whether one-time preprocessing belongs in workflow | **addressed as opt-in**: `emissions.mode: workflow` adds `PrepareEmissions`; `prebuilt` preserves old behavior. This supports reproducible mesh-specific preparation without forcing it on every experiment. |
| #4 | More PACE-product/multichannel templates | **out of scope**; latest uploaded workflow files are preserved unchanged |
| #6 | Anthropogenic iso/mnt placeholders | **preserved/resolved operationally**: CAMS ISO/MNT remain explicit placeholders/common sources for CEDS/CAMS-MIX ensemble members; CEDS NH3 now follows CEDS rather than CAMS |
| #9 | SST update semantics | **out of scope**; latest uploaded workflow behavior is preserved |
| #10 | Daily FINN/GFAS/QFED acquisition + FINN PRM | **directly addressed**: source-first inventory preparation, native GFAS ADS path, QFED daily source handling, FINN GDEX/NRT acquisition, PRM preparation and gap policy |
| #11 | MERRA2/CAMS verification | **out of scope**; no verification behavior changed |

## Issue #2 chemistry-IC choices

The supplied MERRA-IC implementation and issue discussion converge on:

- project background data under `/glade/campaign/ncar/nmmm0081/Data/BKG_DATA`;
- archived MERRA2-GMI aerosol files for the target year;
- archived **2019 monthly OVP** for later-year cases because post-2019 OVP is
  unavailable and daily 2019 coverage is incomplete;
- the existing `run_processing.py` intermediate-file method as the starting
  implementation.

v1.12 integrates that method but makes missing required chemistry source fields
fatal instead of silently writing zeros. This is deliberate for HNO3/NH3/nitrate
provenance.

## Issue #10 PRM clarification

The PRM author clarified that only `prm_lowbc_area_avg` is currently required.
`prm_lowbc_area_std`, `prm_lowbc_frp_avg`, and `prm_lowbc_frp_std` are optional
and missing-data diagnostics should be warnings rather than errors.

v1.12 follows that contract. It still generates all four fields when possible,
but only missing fire-size average is fatal. The uploaded 2024 FINN MOZART file
contains AREA identical to the matching base_FRP file, so the MOZART source can
supply the required PRM field when base_FRP is unavailable.
