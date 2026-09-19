# Anthropogenic NH3 selection

Existing scenarios retain CAMS NH3, independently of their anthropogenic inventory.
To include CEDS NH3 in inventory-ensemble spread, set:

```yaml
model:
  anth nh3 emissions: follow-anth
```

This follows the final anthropogenic inventory after member-variant selection and
scenario overrides: CAMS uses CAMS NH3, CEDS uses CEDS NH3, and CAMS-MIX uses a
logged CAMS fallback because no CAMS-MIX NH3 product is supported here.
Explicit `cams` or `ceds` selects that NH3 inventory for every member. Empty or
omitted values retain CAMS. Unsupported values fail before model launch.

Both initialization and forecast streams use the same selection helper. The
existing missing-file check includes the selected NH3 file; it never silently
substitutes CAMS when a requested CEDS file is absent. No model rebuild is needed.

CEDS files must use the model-compatible variables and units in
`ceds.example.yaml`. That example folds solvents/waste into industry and shipping
into transport to preserve emissions in the categories consumed by the current
model. Selecting an arbitrary raw CEDS file is not sufficient. Validate time
coverage, mesh, units, and active-sector totals against `nh3_anth_sum` before use.
The filename existence check does not validate NetCDF contents.

Other ensemble limitations remain explicit: anthropogenic ISO/MNT and all biogenic
species stay CAMS. Fire NH3 follows the fire inventory, but QFED/GBBEPx fire ISO/MNT
use FINN. Processing additional files alone does not introduce spread in these
shared components. Member-specific scaling of CAMS is a separate experiment.
