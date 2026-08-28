# MPAS-Workflow emissions refactor v1.12 — test report

## Configuration compatibility checks

Using the uploaded `configs.FC.tar` and `configs.DA.tar`:

- Parsed **20** forecast emissions/PRM input streams from the supplied
  `streams.atmosphere`.
- The pre-v1.8 control template exactly matched the supplied 20-stream file.
  The latest PRM interface exposes four `prm_lowbc_*` stream slots, but the PRM
  author clarified that only `prm_lowbc_area_avg` is mandatory. v1.12 keeps the
  four latest-name stream slots while treating the other three as optional.
- Confirmed every CEDS/GFAS/QFED filename selected by `SetStreamsVariant.csh`
  exists in the supplied forecast run listing: **16/16**.
- Confirmed DA `streams.atmosphere_60km` contains no emissions streams; DA state
  includes aerosol species/nitrate but not NH3/HNO3.
- Confirmed forecast cadence: biomass-burning emissions hourly and all four PRM fire-property fields daily.

## New v1.4 logic checks

- Python syntax compilation for every `tools/mpas_emissions/*.py`: **PASS**.
- Scaling helper: species alias `NH3 -> 0.5`: **PASS**.
- Corrected forecast stream parser/manifest: **23 streams** (19 emissions + 4 PRM): **PASS**.
- Regular-grid missing-time bracket (01/03 -> 02, alpha 0.5): **PASS**.
- Maximum interpolation-gap rejection: **PASS**.
- Regional sparse-weight source coverage test: **PASS**.
- FINN long-gap design now supports explicit fallback, no scaling by default,
  optional robust median-ratio scaling, and forced fallback date ranges.
- FINN now writes/stages the separate daily four-field PRM fire-statistics product.

## Retained production validations

- x1.163842 mesh-native SCRIP geometry agreement with production SCRIP.
- CAMS production sparse matrix: 13,972,036 links.
- Maximum destination row-sum error ~1.98e-13.
- Real CAMS January AWB global-integral closure ~1.02e-7.
- Regional meshes permit unmapped global source cells while requiring all MPAS
  destination cells to be covered.
- Generated MPAS-facing outputs are CDF-5 / `NETCDF3_64BIT_DATA`.

## Environment limitation

This container does not provide the Python `netCDF4` package, so a newly written
CDF-5 file could not be executed end-to-end here.  The current v1.12 source compiles and
non-NetCDF tests pass, but Derecho should run one smoke test in the NPL or
`MPAS_EMISSIONS_CONDA_ENV` environment:

1. prepare one CAMS/FINN or GFAS product;
2. `ncdump -k` -> CDF-5 / 64-bit data;
3. run `mpas_emissions.validate_streams` on the resolved forecast streams;
4. for a FINNv2 missing-day test, confirm short gaps interpolate only when
   allowed and the configured long/forced interval uses the fallback source;
5. confirm `.provenance.json` records source choice and scaling factors.

## v1.4 source-first additions

Static/code-path checks completed in this environment:

- Python compilation passed for `mpas_emissions.finn_cli`, the regular-grid
  engine, `mpas_inputs.resource`, `mpas_inputs.merra_chem`, `InitIC.py`, and
  `Cycle.py`.
- Synthetic GDEX-container test passed: a November monthly FINN container with a
  numeric `DAY` field was streamed/extracted to the correct 2022-11-04 and
  2022-11-05 daily cache files.
- Existing regional conservative-weight coverage test passed.
- CEDS selection now resolves anthropogenic NH3 to
  `CEDS_Glb_<year>_MPAS.x1.<nCells>.grid.NH3.nc`.
- The chemistry-enabled init stream now also defines all four PRM input streams. They are consumed by `init_atmosphere` only when the PRM initialization option is enabled in the model namelist.

The current container does not provide Python `netCDF4`, so tests that create
CDF-5 outputs (including the prior end-to-end CAMS test and raw-MERRA variable
validation) cannot execute here. They should be smoke-tested on Derecho in the
same NetCDF-C/CDF-5-capable environment used for production preprocessing.

## Final source-first / ensemble checks

- Table-1 variant labels `cntl, pert01..pert08` are present and retain the
  CAMS/CEDS/CAMS-MIX × FINN/GFAS/QFED 9-member ordering.
- CEDS members resolve anthropogenic NH3 to the CEDS NH3 product; the forecast
  and chemistry-init stream templates both use the same `{{anthNH3}}` selector.
- New/modified YAML examples (`finn`, `ceds`, chemistry source configs,
  source-first scenario, emissions/initic defaults) parse successfully.
- `emissions.seed prebuilt` was added.  `true` preserves transitional CAMS-MIX
  and legacy files; `false` removes the dependency on `EmissionDir/PRMAreaDir`
  when all required products are generated from original resources.
- The shared chemistry cold start uses the scenario-level stream variant; the
  per-member Table-1 emission variants remain a Forecast-time perturbation.

## v1.5 checks

- CEDS v2025 NH3 YAML parses successfully.
- The active MPAS-GOCART2G NH3 crosswalk contains each of the eight supplied
  CEDS source sectors exactly once in the categories currently consumed by NI2G.
- CEDS `365_day` calendar is accepted by the regular-grid time decoder.
- Optional `${CEDS_ANT_DIR}` / `${FINN1_DIR}` source candidates can be absent
  without breaking fallback to campaign/HTTPS sources.
- Legacy FINNv1 URL discovery maps 2024-11-04..2024-11-10 to
  `GLOB_MOZ4_2024309.txt.gz` .. `GLOB_MOZ4_2024315.txt.gz`.
- FINNv1 HTTPS files are lazy: no network transfer occurs until a date is
  selected by the gap/fallback policy.

## PRM tests (updated in v1.12)

- Traced the latest model Registry/init/update code and retained the four
  `prm_lowbc_*` stream definitions.
- Applied the PRM author's clarification: only
  `firesize_biob_modis_avg`/`prm_lowbc_area_avg` is fatal; missing AREA std,
  FRP avg, or FRP std is warning-only.
- Synthetic FINN aggregation matches per-cell population mean/std (`ddof=0`).
- Real uploaded 2024 FINNv2.5.1 MOZART and base_FRP files both parse as valid
  comma-separated files with **40,905** records.
- All checked shared fields are identical row-for-row between the two real
  samples, including AREA, BC, OC, CO, NH3, and SO2; base_FRP adds FRP.
- On the validated x1.163842 SCRIP centers, all 40,905 records map successfully;
  MOZART-derived AREA mean/std exactly matches base_FRP-derived AREA mean/std.
- The real sample therefore confirms that MOZART can supply the current
  mandatory PRM AREA average when a base_FRP file is unavailable.
- Content-based delimiter detection replaces the unsafe filename-based
  base_FRP whitespace assumption.
- Python compilation passed for the modified FINN processor and stream validator.

## v1.10 latest-model PRM checks

- Audited the uploaded `gocartMPAS-main.zip` rather than the older source tree.
- Latest Registry contract comparison: workflow and model both contain exactly
  four `prm_lowbc_*` streams and exactly four
  `firesize_biob_modis_{avg,std}` / `frp_biob_modis_{avg,std}` fields: **PASS**.
- `config_prm_lowbc_interval` is present and the obsolete lbc option/name scan
  is empty: **PASS**.
- GOCART2G init namelist now contains `config_init_bburnPRM = PRMinitFlag`,
  substituted from `doBburnPrm`: **PASS**.
- Synthetic scalar aggregation reproduces per-cell population mean/std: **PASS**.
- AREA-only input is accepted as the current hard PRM contract; optional FRP
  slots are zero-filled with warnings in workflow-generated files: **PASS**.
- Python compilation and YAML parsing after the v1.10 changes: **PASS**.
- This container still lacks Python `netCDF4`, so creation/reading of a new
  CDF-5 output remains a required Derecho smoke test.


## v1.12 final rebase / issue / real-sample checks

- Rebased onto the newly uploaded `MPAS-Workflow-mpas-gocart2g` tree rather than
  the earlier checkout.  A recursive comparison finds **no baseline file missing**
  from v1.12; the latest `config/auto` directory and PACE/DA workflow files are
  preserved.
- Reviewed the current MPAS-Workflow issue set.  Emissions/chemistry scope aligns
  directly with issues #2 and #10 and opt-in preprocessing in #3; #6 placeholders
  remain explicit.  Issues #4, #9, and #11 are deliberately left unchanged.
- `py_compile` passed for all Python under `tools/mpas_emissions`,
  `tools/mpas_inputs`, and `initialize`.
- All 11 emissions/chemistry/default YAML files parsed successfully.
- `test_daily_time_semantics.py`: **PASS**.
- `test_regional_weights.py`: **PASS**.
- `test_prm_optional_contract.py`: **PASS**.  An AREA-average-only PRM file is
  accepted while the three optional lowbc fields emit warnings; missing AREA
  average is fatal.
- `test_end_to_end.py`: **environment-blocked**, not a scientific/code assertion
  failure.  It reaches the MPAS writer and stops because this container lacks the
  Python `netCDF4` package required for CDF-5 `NETCDF3_64BIT_DATA` output.
- Real uploaded FINNv2.5.1 samples: both MOZART and base_FRP contain **40,905**
  data rows; content-based delimiter detection selects comma correctly; all
  checked shared fields are row-for-row identical.
- Real-sample spherical MPAS mapping: all 40,905 records map; 5,523 destination
  cells contain fires; MOZART AREA mean/std equals base_FRP AREA mean/std exactly;
  seven biomass-emission output fields are finite.
- `tcsh -n` could not be run because `tcsh` is not installed in this container;
  run syntax checks on Derecho along with the CDF-5 smoke test.


## v1.12 PRM-source independence

- PASS: `prm source: finn|prebuilt` YAML/Python configuration parses.
- PASS: static contract verifies FINN PRM preparation is activated when
  `do bburn prm: true` and `prm source: finn`, even if `prepare` omits FINN.
- PASS: SetStreamsVariant resolves one PRM file independently of FINN/GFAS/QFED
  biomass-emission selection.
- PASS: uploaded 2024 FINN MOZART sample contains AREA and therefore can supply
  the required PRM fire-size average without the optional base_FRP file.
- NOT IMPLEMENTED BY DESIGN: GFAS FRP or QFED emission flux is not converted to
  FINN-equivalent per-fire area; no validated mapping is available.
