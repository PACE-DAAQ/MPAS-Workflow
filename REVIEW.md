# DRAFT for review: ensemble recenter task + EnsembleForecast component

Branch: `feature/ens_recenter` (off `feature/reorg_paths`)
Review command (run from any checkout of the repo):

```
git diff feature/reorg_paths..feature/ens_recenter
```

Implements the EnKF-off baseline (plan §§1-9) of
`/glade/work/swei/projects/mmm.pace_aod/ensemble_recentering_plan.md`.
Plan §8 (first-cycle seeding) and §12 (optional ensemble EnKF) are intentionally
NOT implemented (TODO markers / left untouched).

## Files created / modified

| File | Status | Purpose |
|---|---|---|
| `bin/RecenterEnsemble.csh` | new | Recenter prev-cycle ensemble forecasts onto this cycle's deterministic analysis; cloned from `bin/RTPP.csh` (plan §4). |
| `config/jedi/applications/ens_recenter.yaml` | new | `{{...}}`-templated production app config for `mpasjedi_ens_recenter.x` (plan §6). |
| `initialize/applications/EnsembleForecast.py` | new | New Component hosting `RecenterEnsemble` + per-member `EnsembleForecast{mm}` tasks (plan §3). |
| `scenarios/defaults/ensembleforecast.yaml` | new | Defaults for the component: `n:0`, `execute:False`, `job:` tree (plan §3b). |
| `scenarios/3dhybrid_OIE60km_WStart.exp4.yaml` | new | Turn-on scenario: `members.n:1`, `ensembleforecast n:27`, 27 `model:member variants`, `SELF.EnsFC` ensemble-B (plan §9). |
| `initialize/framework/Build.py` | edit | Add `EnsRecenterEXE` / `EnsRecenterBuildDir` next to the RTPP entries (plan §7). |
| `initialize/framework/Naming.py` | edit | Register `EnsembleForecast` in `namedComponents` -> produces `EnsembleForecastWorkDir` (plan §7). |
| `initialize/suites/Cycle.py` | edit | Import, instantiate, `export(da.tf.finished)`, add to dependency/task component lists (plan §7). |
| `bin/getCycleVars.csh` | edit | Source `config/auto/ensembleforecast.csh` (safe default `nEnsFCMembers=0`); add `CyclingEnsFC{,IC}Dirs` + `prevCyclingEnsFCDirs` arrays keyed on `$nEnsFCMembers` (plan §5). |

## py_compile / syntax results

- `py_compile`: PASS for `EnsembleForecast.py`, `Build.py`, `Naming.py`, `Cycle.py`.
- `compileall initialize/`: PASS (whole tree still compiles).
- `csh -n`: OK for `bin/RecenterEnsemble.csh` and `bin/getCycleVars.csh`.
- NOT runnable here: `./Run.py` / `cylc` (need env + scenario + allocation). The
  inertness claim (plan §11 step 3: off-state `flow.cylc` byte-identical) was NOT
  verified — see below.

## Key decisions & assumptions

1. **Member count axis.** `ensembleforecast.n` is exported as `nEnsFCMembers`
   (distinct name, following `Members.py`) so a bare `setenv n` cannot collide.
   `self._cshVars = ['nEnsFCMembers']` only (NOT the full vtable), so `n`,
   `updateSea`, etc. are not emitted to csh. Exported unconditionally so
   `config/auto/ensembleforecast.csh` always exists with a value (0 when off).
2. **`self.active = (n > 1 and execute)`.** When inactive, no tasks/dependencies
   are added; only the csh var is exported. Intended to be inert (unverified).
3. **Task/graph wiring.** `RecenterEnsemble` inherits `tf.init`; the
   `EnsembleForecasts` family inherits `tf.execute`; members inherit
   `EnsembleForecasts`. The tf phase graph auto-generates
   `Init...:succeed-all => ...Exec`, i.e. `RecenterEnsemble => EnsembleForecast{mm}`.
   External deps added via `tf.addDependencies`: `daFinished => Pre...` and
   `EnsembleForecastFinished__[-PT6H] => Pre...` (prev-cycle ensemble forecasts).
4. **`job:` nesting.** The default `job:` tree is nested UNDER `ensembleforecast:`
   (the SubConfig root), NOT at top level as drawn in plan §3b. This matches how
   `forecast.yaml` / `rtpp.yaml` actually nest `job:` and is required for
   `Resource(('job', ...))` to resolve. Numbers copied from `forecast.yaml` 60km
   (member forecast) and `rtpp.yaml` 60km (recenterensemble).
5. **RecenterEnsemble.csh reuse.** Kept verbatim from RTPP.csh: invariant-fields
   copy, graph/lookup/Thompson links, streams+namelist sed block,
   `ncdump | grep uReconstruct` precision detection (against the CENTER file), the
   `MPASJEDIVariablesFiles` links, and the run + `grep 'Run: Finishing oops.* with
   status = 0'` status check. Reuses the `rtpp/` model config dir (same Ensemble-mesh
   geometry, per plan §4). `appyaml` is set locally (`ens_recenter.yaml`) rather than
   exported from Python.
6. **Variable lists** (set in RecenterEnsemble.csh, mirroring RTPP.csh:190-219):
   `recenter variables` = `$StandardAnalysisVariables` (met + full GOCART set);
   `state variables` = that superset + `pressure_p air_pressure dry_air_density
   air_potential_temperature u water_vapor_mixing_ratio_wrt_dry_air` +
   `$MPASHydroIncrementVariables`.
7. **Output dirs.** RecenterEnsemble.csh `mkdir -p` each `CyclingEnsFCICDirs[m]` and
   `.../ic/mean` before the run (these are new dirs the MPAS writer needs to exist).
8. **exp4 scenario.** Reproduces exp3 content (exp3 lives on `feature/exp3`, not on
   this base branch) plus the plan §9 additions. `experiment.name` left identical to
   exp3 — see NEEDS HUMAN REVIEW.

## NEEDS HUMAN REVIEW

1. **First-cycle seeding (plan §8) — NOT IMPLEMENTED.** `EnsembleForecast.export()`
   has a `TODO` where the R1-only branch (cold-start each member from pre-staged
   per-member GEFS+MERRA ICs, `/{:02d}` convention) must go. As written, R1's
   `RecenterEnsemble` would have no previous-cycle ensemble input and the
   `Finished__[-PT6H]` dependency has no R1 source. The graph likely needs an R1
   special case (mirror `FirstBackground`) before this can cycle from cold.
2. **PrepareSeaSurfaceUpdate dependency omitted.** Member forecasts run
   `bin/Forecast.csh` with `updateSea=True`. `Forecast.py` additionally makes its
   forecasts depend on `ea['PrepareSeaSurfaceUpdate']`. The plan §3 dependency list
   omits it, so it is omitted here (flagged). If the sea-surface update file is not
   otherwise guaranteed present, add `ea['PrepareSeaSurfaceUpdate'] => tf.pre`.
3. **Guessed job-resource numbers** in `scenarios/defaults/ensembleforecast.yaml`.
   `recenterensemble` uses rtpp.yaml 60km numbers (baseSeconds 30, secondsPerMember
   30, 1 node); member `60km` uses forecast.yaml 60km numbers (2 nodes,
   secondsPerForecastHR 60). Wall-clock for a recenter over 27 members and for 27
   concurrent member forecasts should be sanity-checked against real runs.
4. **Inertness NOT verified.** Plan §11 step 3 (off-state `flow.cylc` regenerates
   byte-identically vs exp3) could not be run here. Please confirm `./Run.py` on a
   non-ensemble scenario is unchanged, especially that the always-written
   `config/auto/ensembleforecast.csh` + the new `getCycleVars.csh` block are truly
   no-ops when `nEnsFCMembers==0`.
5. **`EnsembleForecasts` family / `EnsembleForecast{mm}` task names.** Confirm these
   don't collide with any cylc task naming elsewhere and that inheriting the tf
   `init`/`execute` families yields the intended `succeed-all` gating.
6. **exp4 `experiment.name`** is identical to exp3 (`3dhybrid_OIE60km`), so it would
   reuse exp3's experiment directory. Rename if exp4 must be isolated.
7. **`mpasjedi_ens_recenter.x` must be built** in the bundle (plan §2). Out of scope
   here (bundle repo not modified); `Build.py` now points at
   `{mpas bundle}/bin/mpasjedi_ens_recenter.x`.

## Out of scope (untouched, per instructions)

- `Variational.py`, `DA.py`, `EnKF.py`, and all deterministic-DA-path files.
- `mpas-bundle` repo (executable assumed already built + validated).
- Plan §12 optional ensemble EnKF.
