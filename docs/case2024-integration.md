# July–September 2024 local integration

This branch integrates workflow PR29's stack, PR34 and PR14 locally; it has not merged them upstream.

Opt-in scenario model settings:

```yaml
model:
  online emission factors: true
  dust emission factor: 0.4
  seasalt emission factor: 1.0
  emission member table: /absolute/path/to/members.csv
  carry persistent hno3: true
  carry land state: true
```

The CSV contains sequential `member` IDs, `anth`, `biob`, `biog`, `dust_relative`, and `seasalt_relative`. Its row count must equal ensembleforecast.n. All factors are finite and nonnegative. Effective online factors multiply the central factor by the member's relative factor. Explicit ensemble-role routing prevents the deterministic member 1 from selecting ensemble row 1. The table overrides inventory choices for ensemble forecasts only. Central inventories retain their configured choices. Online-factor options require the matching runtime-factor model build.

`carry persistent hno3: false` retains the cold target IC's persistent_hno3 (initialized from background_hno3), rather than zeroing HNO3 or removing nitrate. Land carry is a separate option. With DA and refreshed cold meteorology, non-analysis chemistry precursors, HNO3 (if enabled), and land fields (if enabled) come from the same member's previous forecast; analyzed aerosol fields come from JEDI. This avoids relying on JEDI to serialize non-analysis fields. Missing requested auxiliary fields fail explicitly. Non-analysis precursor selection follows the current StandardAnalysisVariables list; update that mapping if the analyzed variables change.

`build.ens recenter directory` selects an independently built recenter tool. `build.forecast environment script` permits the standalone Intel model to use its compiler/MPI environment independently of the GNU JEDI bundle.

Other local fixes: retain InitIC and ensemble dependencies together; require surface input preparation before member forecasts; omit unused NH3 FEF/SHP fields from the input stream, since shared CAMS inputs do not contain them and the model's NH3 injection does not consume them.

Validation: helper tests cover central/12-member routing, table length, invalid factors, HNO3 reset vs carry, identical six-field land carry, and distinct analysis vs prior-member sources. Both smoke scenarios pass Cylc validation. All 13 seed configurations pass 23 emission/PRM stream checks, including nonzero fire-size input. Full cycling execution is still pending seed completion; these checks are not an end-to-end pass.

PR35 Copilot review: the OpenMP full-block Thompson reconstruction race is valid. Budget scratch should also be guarded and limited to the ten budgeted species. Existing validation and the current seeds use OMP_NUM_THREADS=1; a threaded model regression remains required before recommending PR35 merge. No change to PR35 has been pushed during this integration.
