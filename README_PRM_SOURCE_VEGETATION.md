# Optional fire-source vegetation input

Requires a model containing [gocartMPAS PR #45](https://github.com/PACE-DAAQ/gocartMPAS/pull/45). Disabled by default: existing namelists, streams, pollutant inventories and production inputs are unchanged. Enable only for a new experiment using a compatible executable.

The model's vegetation category is independent of the chosen pollutant inventory (QFED, GFAS, FINN or GBBEPx). This adapter uses the **same FINN records as daily plume fire size**, not vegetation inferred from pollutant flux. It does not fill missing fires or alter emission magnitude.

## Workflow preparation

```yaml
emissions:
  mode: workflow
  prm source vegetation: true
  prm vegetation manifest: /absolute/path/manifest.csv
  prm vegetation fire size file: /absolute/path/staged_daily_fire_size.nc
  prm vegetation file: prm_source_vegetation.nc
```

Keep the existing emissions configuration, including mesh, period, FINN preparation and QFED selection. The fire-size path must point to the file produced/seeded by preparation (or the authoritative prebuilt equivalent). Preparation runs the bundled generator after other emissions products succeed. Output lands in the existing emissions work directory. The manifest is explicit:

```csv
date,path
2024-09-10,/absolute/path/actual_FINN_source_for_20240910.txt.gz
2024-09-11,/absolute/path/actual_FINN_source_for_20240911.txt.gz
```

List every UTC day in order, including the actual fallback source when FINN NRT is missing. No manifest auto-discovery or interpolation is attempted. Each day must reproduce the staged cellwise mean fire size; mismatches fail. This first integration supports global nearest-cell mapping (including global variable-resolution meshes). Regional rejection/interior-only mapping is not supported and is rejected when enabled.

The generator retains the matched fire-size timestamp and writes area-weighted dominant vegetation, coverage and mixture diagnostics, source hashes and mesh-coordinate fingerprint. Unknown vegetation retains the model's legacy classification. See the generator's crosswalk and gocartMPAS PR #45 for category definitions.

## Standalone/prebuilt route

```bash
python tools/prm_source_vegetation.py \
  --mesh invariant.nc --manifest manifest.csv \
  --prm-input staged_daily_fire_size.nc \
  --output prm_source_vegetation.nc --reuse-validated
```

Place the resulting file in Build's PRM area directory for prebuilt mode, and enable `emissions: prm source vegetation: true` with its basename. Workflow mode uses the experiment-local emissions directory instead. No installed gocartMPAS tools checkout is needed; the generator is bundled here (ported from gocartMPAS PR #45, c8b154ee).

`--reuse-validated` verifies content hashes of the mesh, manifest, raw sources, reference fire-size file, generator and output before reuse. It fails on stale or unverified existing files rather than replacing them. Choose a new filename or explicitly regenerate a separate copy. Hashing large inputs has an I/O cost, but no regridding or input modification is performed on reuse. Concurrent preparation targeting the same output is not supported; keep one preparation task per work directory.

## Forecast staging

When explicitly enabled, Forecast validates categories, mesh coordinates/cell order, exact full timestamp agreement with the staged fire-size file, daily continuity and forecast-period coverage before adding the input stream and `config_prm_source_vegetation = true`. The input stream references the absolute prepared file. PRM polling is set to hourly **only in this opt-in path**, so non-midnight forecasts select the next daily record at its UTC timestamp. Hourly polling does not create hourly fire observations.

When disabled or absent, the forecast branch does nothing: even older executables do not see the new namelist key or stream. Do not enable this setting with an executable predating gocartMPAS PR #45.

## Validation

Run in Python with numpy, scipy and netCDF4 installed, plus tcsh for the disabled-shell regression:

```bash
python -m unittest discover -s test -p 'test_prm_source_vegetation*.py' -v
```

Tests use small actual NetCDF inputs and include timestamp preservation, aggregation, provenance, rejection/cleanup, unchanged reuse, changed-source rejection, mesh-order and category validation, forecast coverage and an execution of the actual disabled Forecast shell block. No full Cylc campaign or new model forecast was run for this workflow-only PR.
