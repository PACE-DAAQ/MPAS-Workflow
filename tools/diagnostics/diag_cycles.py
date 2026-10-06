#!/usr/bin/env python
"""Cycle-by-cycle DA diagnostics for one MPAS-JEDI cycling run.

Usage:
  python diag_cycles.py EXP [--root DIR] [--out DIR] [--cycles 2024072100-2024073118]

EXP is the run name under --root (default /glade/derecho/scratch/syha/pandac), e.g. syha_prod_oci550.
Writes <out>/<EXP>_cycles.md (tables), <out>/<EXP>_cycles.json and <out>/<EXP>_cycles.png.

What it reports per cycle:
  - Variational runtime (min), total memory, final/initial J
  - for every dbOut/obsout_da_*.h5: n assimilated / n in file, O-B and O-A RMS and mean,
    Jo/n (from EffectiveError0) for assimilated obs; passive observers (monitoring only,
    O-A == O-B) are listed under "passive"
  - AOD observers: 550 nm channel (or nearest); AERONET: 500 nm channel; PM in ug/m3
  - analysis increment RMS for theta, qv, u, and the main aerosol tracers
Run on Derecho with:  module load conda; conda activate npl
"""
import argparse, glob, os, re, json, sys
import numpy as np, h5py

AOD_VAR = 'aerosolOpticalDepth'
PM_VARS = {'particulatematter2p5Surface': ('PM2.5', 1e9), 'particulatematter10Surface': ('PM10', 1e9)}
INC_VARS = [('theta', 'theta'), ('qv', 'qv'), ('uReconstructZonal', 'u'), ('qocphilic', 'OC'), ('qbcphilic', 'BC'),
            ('qso4', 'SO4'), ('qdust2', 'du2'), ('qseas2', 'ss2')]


def ok(a):
    return np.isfinite(a) & (np.abs(a) < 1e29)


def channel_for(f, target_nm):
    """Index of the channel closest to target_nm using sensorCentralFrequency (Hz) or sensorCentralWavelength."""
    md = f['MetaData']
    if 'sensorCentralWavelength' in md:
        wl = md['sensorCentralWavelength'][:]
        wl = wl[0] if wl.ndim == 2 else wl
        if wl.max() < 100:  # micrometers
            wl = wl * 1e3
    elif 'sensorCentralFrequency' in md:
        fr = md['sensorCentralFrequency'][:]
        fr = fr[0] if fr.ndim == 2 else fr
        wl = 2.99792458e17 / fr
    else:
        return 0, None
    i = int(np.argmin(np.abs(wl - target_nm)))
    return i, float(wl[i])


def hofx_stats(f, var, ch=None, scale=1.0):
    """Departures from an offline HofX file (obsout_hofx_*.h5): O-B only, no analysis."""
    r = {}
    if 'ObsValue' not in f or 'hofx' not in f or var not in f['hofx']:
        return None            # empty obs space (no observations in the archive for this cycle)
    obs = f['ObsValue'][var][:]; hx = f['hofx'][var][:]
    q = f['EffectiveQC'][var][:] if 'EffectiveQC' in f else np.zeros_like(obs)
    e = f['EffectiveError'][var][:] if 'EffectiveError' in f else None
    if obs.ndim == 2:
        obs, hx, q = obs[:, ch], hx[:, ch], q[:, ch]
        e = e[:, ch] if e is not None else None
    d = obs - hx
    m = ok(d) & ok(obs) & ok(hx) & (q == 0)
    r['n_file'] = int(ok(obs).sum()); r['n'] = int(m.sum()); r['passive'] = True
    if m.any():
        r['OmB_rms'] = float(np.sqrt(np.mean(d[m] ** 2))) * scale
        r['OmB_mean'] = float(d[m].mean()) * scale
        if e is not None and np.isfinite(e[m]).all() and (e[m] > 0).all():
            r['JoB'] = float(np.mean((d[m] / e[m]) ** 2))
    return r


def stats(f, var, ch=None, scale=1.0):
    r = {}
    if 'ombg' not in f:
        return hofx_stats(f, var, ch, scale)
    ob = f['ombg'][var][:]; oa = f['oman'][var][:]
    q0 = f['EffectiveQC0'][var][:]; q1 = f['EffectiveQC1'][var][:]
    e0 = f['EffectiveError0'][var][:] if 'EffectiveError0' in f else None
    if ob.ndim == 2:
        ob, oa, q0, q1 = ob[:, ch], oa[:, ch], q0[:, ch], q1[:, ch]
        e0 = e0[:, ch] if e0 is not None else None
    m0 = ok(ob) & (q0 <= 1); m1 = ok(oa) & (q1 <= 1)   # flag 1 = passive (monitor-only obs such as AERONET) is valid for departures
    r['n_file'] = int(ok(ob).sum()); r['n'] = int(m0.sum())
    if m0.any():
        r['OmB_rms'] = float(np.sqrt(np.mean(ob[m0] ** 2))) * scale; r['OmB_mean'] = float(ob[m0].mean()) * scale
        if e0 is not None: r['JoB'] = float(np.mean((ob[m0] / e0[m0]) ** 2))
    if m1.any():
        r['OmA_rms'] = float(np.sqrt(np.mean(oa[m1] ** 2))) * scale; r['OmA_mean'] = float(oa[m1].mean()) * scale
        if e0 is not None: r['JoA'] = float(np.mean((oa[m1] / e0[m1]) ** 2))
    both = m0 & m1
    r['passive'] = bool(both.any() and np.allclose(ob[both], oa[both]))
    return r


def cycle(cdir):
    r = {}
    lg = f'{cdir}/run/mem001/jedi.log'
    if os.path.exists(lg):
        J = []
        for line in open(lg, errors='ignore'):
            if 'OOPS_STATS Run end' in line:
                mm = re.search(r'Runtime:\s+([\d.]+) sec.*total:\s+([\d.]+) G', line)
                if mm: r['min'] = float(mm.group(1)) / 60; r['GB'] = float(mm.group(2))
            mm = re.search(r'CostFunction: Nonlinear J\s*=\s*([\d.eE+-]+)', line)
            if mm: J.append(float(mm.group(1)))
        if J: r['J0'] = J[0]; r['J1'] = J[-1]
    obs = {}
    for fn in sorted(glob.glob(f'{cdir}/dbOut/obsout_da_*.h5')) + sorted(glob.glob(f'{cdir}/dbOut/obsout_hofx_*.h5')):
        inst = os.path.basename(fn).split('_', 2)[2][:-3]
        try:
            f = h5py.File(fn)
        except Exception:
            continue
        grp = 'ombg' if 'ombg' in f else 'ObsValue'
        if grp not in f:
            continue           # empty obs space (no observations in the archive for this cycle)
        if AOD_VAR in f[grp]:
            nm = 500 if 'aeronet' in inst else 550
            ch, wl = channel_for(f, nm)
            key = f'{inst}@{wl:.0f}nm' if wl else inst
            result = stats(f, AOD_VAR, ch)
            if result is not None:
                obs[key] = result
        for v, (lab, sc) in PM_VARS.items():
            if v in f[grp]:
                result = stats(f, v, scale=sc)
                if result is not None:
                    obs[f'{inst}:{lab}'] = result
        for v in ['airTemperature', 'windEastward', 'specificHumidity', 'stationPressure']:
            if v in f[grp]: obs[f'{inst}:{v}'] = stats(f, v)
    r['obs'] = obs
    an = glob.glob(f'{cdir}/an/an.*.nc'); bg = glob.glob(f'{cdir}/bg/bg.*.nc')
    if an and bg:
        try:
            import netCDF4 as nc
            A = nc.Dataset(an[0]); B = nc.Dataset(bg[0]); A.set_auto_mask(False); B.set_auto_mask(False)
            r['inc'] = {lab: float(np.sqrt(np.mean((A[v][0] - B[v][0]) ** 2))) for v, lab in INC_VARS if v in A.variables}
        except Exception as e:
            r['inc'] = {}
    return r


def g(d, *ks):
    for k in ks:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d if d != {} else np.nan


def monitored_observers(cdir):
    """Names of the obs spaces flagged 'monitoring only: true' in the cycle's DA yaml.

    The yaml is scanned as text (PyYAML rejects the '%' in some file-name templates):
    each 'obs space:' line opens a new observer, whose identity is taken from the first
    'obsfile:' line after it (dbIn/<observer>_obs_<cycle>.h5, since 'name:' is the generic
    operator name such as AodCRTM); a 'monitoring only: true' line before the next observer
    marks it as a monitor.
    Returns an empty set when no yaml is found; the caller then falls back to O-A == O-B.
    """
    names = set()
    yaml_files = sorted(glob.glob(f'{cdir}/3dhybrid*.yaml')) or sorted(glob.glob(f'{cdir}/*.yaml'))
    if not yaml_files:
        return names
    current_observer = None
    expecting_file = False
    for line in open(yaml_files[0]):
        text = line.strip()
        if text.endswith('obs space:'):
            expecting_file = True
            continue
        if expecting_file and text.startswith('obsfile:'):
            file_name = os.path.basename(text.split(':', 1)[1].strip())
            current_observer = file_name.split('_obs_')[0]
            expecting_file = False
            continue
        if text.startswith('monitoring only:') and 'true' in text.lower() and current_observer:
            names.add(current_observer)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exp'); ap.add_argument('--root', default='/glade/derecho/scratch/syha/pandac')
    ap.add_argument('--out', default='.'); ap.add_argument('--cycles', default=None, help='YYYYMMDDHH-YYYYMMDDHH')
    ap.add_argument('--noplot', action='store_true')
    a = ap.parse_args()
    cdirs = sorted(glob.glob(f'{a.root}/{a.exp}/CyclingDA/20*')) or sorted(glob.glob(f'{a.root}/{a.exp}/20*'))
    if a.cycles:
        c0, c1 = a.cycles.split('-'); cdirs = [c for c in cdirs if c0 <= os.path.basename(c) <= c1]
    rows = {}
    for cdir in cdirs:
        if not (glob.glob(f'{cdir}/dbOut/obsout_da_*.h5') or glob.glob(f'{cdir}/dbOut/obsout_hofx_*.h5')): continue
        rows[os.path.basename(cdir)] = cycle(cdir)
    if not rows: sys.exit(f'no completed cycles for {a.exp}')
    os.makedirs(a.out, exist_ok=True)
    json.dump(rows, open(f'{a.out}/{a.exp}_cycles.json', 'w'), indent=1, default=float)
    keys = sorted({k for r in rows.values() for k in r['obs']})
    # an observer is 'passive' when the DA yaml says monitoring only, or when O-A == O-B in every cycle
    monitors = set()
    for cdir in cdirs:
        monitors |= monitored_observers(cdir)

    def is_passive(key):
        observer_name = key.split('@')[0]
        if observer_name in monitors:
            return True
        return all(r['obs'].get(key, {}).get('passive', True) for r in rows.values())

    assim = [k for k in keys if not is_passive(k)]
    passive = [k for k in keys if is_passive(k)]
    out = [f'# {a.exp}: {len(rows)} cycles {min(rows)}..{max(rows)}\n']
    out.append('## cost\n| cycle | DA min | GB | J1/J0 |\n|---|---|---|---|')
    for c, r in rows.items():
        out.append(f"| {c} | {r.get('min', np.nan):.1f} | {r.get('GB', np.nan):.0f} | {r.get('J1', np.nan) / r.get('J0', np.nan):.2f} |")
    for title, ks in [('assimilated', assim), ('passive (monitor only)', passive)]:
        for k in ks:
            out.append(f'\n## {k} ({title})\n| cycle | n assim / n file | O-B rms / mean | O-A rms / mean | mean sq. norm. departure B -> A |\n|---|---|---|---|---|')
            for c, r in rows.items():
                s = r['obs'].get(k)
                if not s: out.append(f'| {c} | - | - | - | - |'); continue
                out.append(f"| {c} | {s['n']} / {s['n_file']} | {s.get('OmB_rms', np.nan):.3f} / {s.get('OmB_mean', np.nan):+.3f} | {s.get('OmA_rms', np.nan):.3f} / {s.get('OmA_mean', np.nan):+.3f} | {s.get('JoB', np.nan):.2f} -> {s.get('JoA', np.nan):.2f} |")
    labs = [lab for _, lab in INC_VARS]
    out.append('\n## increment RMS (analysis - background)\n| cycle | ' + ' | '.join(labs) + ' |\n|---|' + '---|' * len(labs))
    for c, r in rows.items():
        out.append(f'| {c} | ' + ' | '.join(f"{r.get('inc', {}).get(l, np.nan):.2e}" for l in labs) + ' |')
    txt = '\n'.join(out); open(f'{a.out}/{a.exp}_cycles.md', 'w').write(txt); print(txt)
    if a.noplot:
        return
    plot_time_series(rows, assim, passive, keys, a.exp, f'{a.out}/{a.exp}_cycles.png')


def plot_time_series(rows, assim, passive, keys, exp, out_png):
    """Three panels over cycles: RMS, mean departure, mean squared normalized departure.

    Line style convention: dashed = background (O-B), solid = analysis (O-A); one colour per observer.
    Passive (monitor-only) observers are labelled '(monitor)' in the RMS and mean panels and left out of
    the bottom panel, which shows the mean squared normalized departure  (1/n) sum ((O-B)/sigma_o)^2
    (and the same with O-A) for assimilated observers only, with the available (in file) and
    assimilated observation counts as grey bars on a right-hand axis.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    cycles = list(rows)
    t = np.arange(len(cycles))
    tick_labels = [c[4:10] for c in cycles]                     # MMDDHH
    aod_keys = [k for k in keys if 'nm' in k]

    def series(key, field):
        return [rows[c]['obs'].get(key, {}).get(field, np.nan) for c in cycles]

    fig, ax = plt.subplots(3, 1, figsize=(12, 11), sharex=True)
    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']

    for i, key in enumerate(aod_keys):
        color = colors[i % len(colors)]
        tag = ' (monitor)' if key in passive else ''

        # panel 0: RMS of O-B (dashed) and O-A (solid)
        ax[0].plot(t, series(key, 'OmB_rms'), 'o--', color=color, ms=4, label=f'{key}{tag} O-B')
        ax[0].plot(t, series(key, 'OmA_rms'), 's-', color=color, ms=4, label=f'{key}{tag} O-A')

        # panel 1: mean departures
        ax[1].plot(t, series(key, 'OmB_mean'), 'o--', color=color, ms=4, label=f'{key}{tag} O-B mean')
        ax[1].plot(t, series(key, 'OmA_mean'), 's-', color=color, ms=4, label=f'{key}{tag} O-A mean')

        # panel 2: mean squared normalized departure, assimilated observers only
        # (for a monitor the nominal error carries no information, so its value is meaningless)
        if key in passive:
            continue
        ax[2].plot(t, series(key, 'JoB'), 'o--', color=color, ms=4, label=f'{key} O-B')
        ax[2].plot(t, series(key, 'JoA'), 's-', color=color, ms=4, label=f'{key} O-A')

    ax[0].set_ylabel('RMS')
    ax[1].set_ylabel('mean O-B / O-A')
    ax[1].axhline(0, color='k', lw=0.5)
    ax[2].set_ylabel('mean squared normalized departure')
    ax[2].axhline(1, color='k', lw=0.5)

    # observation counts on a right-hand axis of the bottom panel: available (in file) vs assimilated,
    # as side-by-side bars per cycle; one pair of bars per assimilated observer
    count_ax = ax[2].twinx()
    assimilated_keys = [k for k in aod_keys if k not in passive]
    bar_width = 0.8 / max(1, 2 * len(assimilated_keys))
    for i, key in enumerate(assimilated_keys):
        offset = (2 * i - len(assimilated_keys)) * bar_width + bar_width / 2
        count_ax.bar(t + offset, series(key, 'n_file'), bar_width, color='0.85', edgecolor='0.5',
                     label=f'{key} available')
        count_ax.bar(t + offset + bar_width, series(key, 'n'), bar_width, color='0.55', edgecolor='0.3',
                     label=f'{key} assimilated')
    count_ax.set_ylabel('observation count')
    count_ax.legend(fontsize=10, loc='upper right')
    ax[2].set_zorder(count_ax.get_zorder() + 1)      # keep the innovation lines in front of the bars
    ax[2].patch.set_visible(False)

    # leave 45% headroom in every panel so the legends do not cover the data
    for panel in list(ax) + [count_ax]:
        panel.grid(alpha=0.3)
        low, high = panel.get_ylim()
        if panel is count_ax:
            low = 0
        panel.set_ylim(low, low + 1.45 * (high - low))
    ax[0].legend(fontsize=10, ncol=2, loc='upper left')
    ax[1].legend(fontsize=10, ncol=2, loc='upper left')
    ax[2].legend(fontsize=10, ncol=1, loc='upper left')
    ax[2].set_xticks(t)
    ax[2].set_xticklabels(tick_labels, rotation=90, fontsize=9)

    fig.suptitle(f'{exp}: dashed = background (O-B), solid = analysis (O-A)')
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    print('wrote', out_png)


if __name__ == '__main__':
    main()
