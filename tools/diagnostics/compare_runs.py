#!/usr/bin/env python
"""Side-by-side comparison of several cycling runs over their common cycles.

Usage:
  python compare_runs.py EXP1 EXP2 [EXP3 ...] [--labels A,B,...] [--root DIR] [--out DIR] [--cycles c0-c1]

Runs diag_cycles.cycle() on each run and prints one table per observer/quantity with one column per run
(O-B rms / O-A rms, n assimilated), a DA-cost table, and an increment table; plus <out>/compare_<...>.png
with O-B and O-A RMS of every AOD observer per run. Use it e.g. for exp1 vs exp1-QFED-B, or exp1 vs polar vs VIIRS.
"""
import argparse, glob, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diag_cycles import cycle, INC_VARS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exps', nargs='+'); ap.add_argument('--labels', default=None)
    ap.add_argument('--root', default='/glade/derecho/scratch/syha/pandac'); ap.add_argument('--out', default='.')
    ap.add_argument('--cycles', default=None); ap.add_argument('--all', action='store_true', help='all cycles, not only common ones')
    a = ap.parse_args()
    labels = a.labels.split(',') if a.labels else a.exps
    R = {}
    for e, l in zip(a.exps, labels):
        cds = sorted(glob.glob(f'{a.root}/{e}/CyclingDA/20*'))
        if a.cycles:
            c0, c1 = a.cycles.split('-'); cds = [c for c in cds if c0 <= os.path.basename(c) <= c1]
        R[l] = {os.path.basename(c): cycle(c) for c in cds if glob.glob(f'{c}/dbOut/obsout_da_*.h5')}
    cycles = sorted(set.union(*[set(r) for r in R.values()])) if a.all else sorted(set.intersection(*[set(r) for r in R.values()]))
    if not cycles: sys.exit('no common cycles')
    keys = sorted({k for r in R.values() for c in r.values() for k in c['obs']})
    out = [f"# {' vs '.join(labels)}: cycles {cycles[0]}..{cycles[-1]} ({len(cycles)})\n"]
    out.append('## DA minutes\n| cycle | ' + ' | '.join(labels) + ' |\n|---|' + '---|' * len(labels))
    for c in cycles: out.append(f'| {c} | ' + ' | '.join(f"{R[l].get(c, {}).get('min', np.nan):.1f}" for l in labels) + ' |')
    for k in keys:
        out.append(f'\n## {k}: O-B rms / O-A rms (n assim)\n| cycle | ' + ' | '.join(labels) + ' |\n|---|' + '---|' * len(labels))
        for c in cycles:
            cells = []
            for l in labels:
                s = R[l].get(c, {}).get('obs', {}).get(k)
                cells.append(f"{s.get('OmB_rms', np.nan):.3f} / {s.get('OmA_rms', np.nan):.3f} ({s['n']})" if s else '-')
            out.append(f'| {c} | ' + ' | '.join(cells) + ' |')
    out.append('\n## increment RMS\n| cycle | field | ' + ' | '.join(labels) + ' |\n|---|---|' + '---|' * len(labels))
    for c in cycles:
        for _, f in INC_VARS:
            out.append(f'| {c} | {f} | ' + ' | '.join(f"{R[l].get(c, {}).get('inc', {}).get(f, np.nan):.2e}" for l in labels) + ' |')
    txt = '\n'.join(out); os.makedirs(a.out, exist_ok=True)
    tag = '_'.join(labels).replace(' ', '').replace('/', '')
    open(f'{a.out}/compare_{tag}.md', 'w').write(txt); print(txt)
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    aodk = [k for k in keys if 'nm' in k]
    fig, ax = plt.subplots(len(aodk), 1, figsize=(11, 3 * len(aodk)), sharex=True, squeeze=False); t = np.arange(len(cycles))
    for i, k in enumerate(aodk):
        for l in labels:
            ax[i, 0].plot(t, [R[l].get(c, {}).get('obs', {}).get(k, {}).get('OmB_rms', np.nan) for c in cycles], 'o-', ms=3, label=f'{l} O-B')
            ax[i, 0].plot(t, [R[l].get(c, {}).get('obs', {}).get(k, {}).get('OmA_rms', np.nan) for c in cycles], 's--', ms=3, label=f'{l} O-A')
        ax[i, 0].set_ylabel(f'{k}\nRMS'); ax[i, 0].grid(alpha=.3); ax[i, 0].legend(fontsize=7, ncol=len(labels))
    ax[-1, 0].set_xticks(t); ax[-1, 0].set_xticklabels([c[4:10] for c in cycles], rotation=90, fontsize=7)
    fig.tight_layout(); fig.savefig(f'{a.out}/compare_{tag}.png', dpi=110); print('wrote', f'{a.out}/compare_{tag}.png')


if __name__ == '__main__':
    main()
