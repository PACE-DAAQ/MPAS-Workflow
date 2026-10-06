# DA diagnostics for MPAS-JEDI cycling runs

| script | what it does | example |
|---|---|---|
| diag_cycles.py | one run, all completed cycles: DA cost, per-observer n/O-B/O-A/Jo/n (assimilated and passive observers), increments; writes EXP_cycles.md/.json/.png | `python diag_cycles.py EXP --root DIR --out OUT` |
| compare_runs.py | several runs side by side over common cycles; writes compare_*.md/.png | `python compare_runs.py EXP1 EXP2 --labels a,b --root DIR --out OUT` |

Runs are read from `<root>/<EXP>/CyclingDA/<cycle>/dbOut/obsout_da_*.h5`. Restrict cycles with `--cycles YYYYMMDDHH-YYYYMMDDHH`.
The default `--root` is a site-specific scratch path; pass `--root` for other users' runs.
