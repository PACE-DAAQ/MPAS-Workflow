# GOCART2G cycling controls (July–September 2024 case)

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

`model: member variants` follows the same rule: a deterministic central forecast (`members: n` ≤ 1) keeps the scenario-wide `streams variant`, while EnsembleForecast members and the members of a multi-member central ensemble (`members: n` > 1) use their row of `member variants`.

`carry persistent hno3: false` retains the cold target IC's persistent_hno3 (initialized from background_hno3), rather than zeroing HNO3 or removing nitrate. Land carry is a separate option. With DA and refreshed cold meteorology, non-analysis chemistry precursors, HNO3 (if enabled), and land fields (if enabled) come from the same member's previous forecast; analyzed aerosol fields come from JEDI. This avoids relying on JEDI to serialize non-analysis fields. Missing required chemistry fields fail explicitly; requested land fields that are absent are reported and skipped. Non-analysis precursor selection follows the current StandardAnalysisVariables list; update that mapping if the analyzed variables change.

`build.ens recenter directory` selects an independently built recenter tool. `build.forecast environment script` permits the standalone Intel model to use its compiler/MPI environment independently of the GNU JEDI bundle.

Related changes: retain InitIC and ensemble dependencies together; require surface input preparation before member forecasts; omit unused NH3 FEF/SHP fields from the input stream, since shared CAMS inputs do not contain them and the model's NH3 injection does not consume them.

Tests: `test/case2024/test_routing.py` covers central/12-member routing, table length and invalid factors; `test/case2024/test_hno3_carry.py` covers HNO3 reset vs carry, identical six-field land carry, and distinct analysis vs prior-member sources.
