#!/usr/bin/env python3

'''
 (C) Copyright 2026 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''

from initialize.applications.ExtendedForecast import ExtendedForecast
from initialize.applications.Forecast import Forecast
from initialize.applications.InitIC import InitIC
from initialize.applications.Members import Members

from initialize.config.Config import Config

from initialize.data.ExternalAnalyses import ExternalAnalyses
from initialize.data.Emissions import Emissions
from initialize.data.FirstBackground import FirstBackground
from initialize.data.Model import Model
from initialize.data.Observations import Observations
from initialize.data.InvariantStream import InvariantStream
from initialize.data.StateEnsemble import StateEnsemble

from initialize.framework.Build import Build
from initialize.framework.Experiment import Experiment
from initialize.framework.Naming import Naming

from initialize.suites.SuiteBase import SuiteBase


class ForecastOnlyCycle(SuiteBase):
  '''Cycling forecast with no data assimilation.

  Why this exists: neither existing suite does met-replaced, chemistry-chained
  cycling.

    * ForecastFromExternalAnalyses does not chain. Its IC is
      externalanalyses.outputs['state']['Outer'] with icType 'external', so each
      cycle is an independent forecast from that cycle's analysis and nothing --
      aerosol least of all -- accumulates across cycles.
    * Cycle chains, but its forecast IC is da.outputs['state']['members'] and
      Variational allows no 'none' DAType, so it cannot be run without DA.

  This suite is Cycle minus DA: the forecast IC is the PREVIOUS cycle's own
  forecast, so CHEMISTRY carries forward, while meteorology is refreshed from the
  external analysis each cycle by bin/Forecast.csh (forecast:
  updateATMVarsFromCold -- a Forecast option, not a Model one).

  Land state does NOT carry forward. tools/copy_mpas_vars.py grew an
  --include-land flag for soil and snow, but bin/Forecast.csh invokes it with the
  two positional paths only and no configuration reaches the flag, so cycling is
  chemistry-only. Wiring it needs another positional argument to Forecast.csh,
  which would collide with the restart-interval argument already on
  mpas-gocart2g; worth doing once this stack is rebased onto that, and not worth
  a merge conflict before then. That gives a reference/free-cycling run, and a
  spin-up whose meteorology stays on the rails.

  The typical use is a background-error-covariance sample set: cycle 6-hourly
  and launch an extended forecast at a chosen UTC hour (extendedforecast:
  meanTimes) to produce one sample per day. Note that the first cycle point
  produces no sample -- it has no previous-cycle state to launch from -- so an
  N-day window yields N-1 samples.

  Set 'forecast: post: []' unless the background verification is wanted: it
  defaults to ['verifymodel'], and VerifyModelBG compares against a 'diag'
  product that this suite's ExternalAnalysisToMPAS does not write, so the task
  fails every cycle. The failures do not block cycling, but they bury real ones.
  '''

  def __init__(self, conf:Config):
    super().__init__(conf)

    self.c['model'] = Model(conf)
    self.c['build'] = Build(conf, self.c['model'])
    meshes = self.c['model'].getMeshes()
    self.c['emissions'] = Emissions(conf, self.c['hpc'], meshes['Outer'])
    self.c['observations'] = Observations(conf, self.c['hpc'])
    self.c['members'] = Members(conf)

    self.c['externalanalyses'] = ExternalAnalyses(conf, self.c['hpc'], meshes)
    self.c['initic'] = InitIC(conf, self.c['hpc'], meshes, self.c['externalanalyses'],
                self.c['emissions'], self.c['workflow'])

    # The warm IC is this suite's own previous-cycle forecast. Forecast.export
    # publishes exactly this as outputs['state']['members'], but that is only
    # available after construction, so build the equivalent StateEnsemble here
    # from Forecast's class-level workDir/forecastPrefix. If those class
    # attributes ever change, this must change with them -- asserted below.
    assert Forecast.workDir == 'CyclingFC', \
      f'Forecast.workDir changed to {Forecast.workDir!r}; update ForecastOnlyCycle'
    assert Forecast.forecastPrefix == 'mpasout', \
      f'Forecast.forecastPrefix changed to {Forecast.forecastPrefix!r}; update ForecastOnlyCycle'

    members = self.c['members']
    warmIC = StateEnsemble(meshes['Outer'])
    for mm in range(1, members.n+1, 1):
      warmIC.append({
        'directory': Forecast.workDir+'/{{prevCycleDate}}'+members.memFmt.format(mm),
        'prefix': Forecast.forecastPrefix,
      })

    self.c['forecast'] = Forecast(conf, self.c['hpc'], meshes['Outer'], members,
                self.c['model'], self.c['observations'], self.c['workflow'],
                self.c['externalanalyses'], warmIC, self.c['emissions'])

    # Cycle 1 has no previous forecast; FirstBackground seeds it from the cold
    # external analysis exactly as in Cycle.
    self.c['firstbackground'] = FirstBackground(conf, self.c['hpc'], meshes, members,
                self.c['workflow'], self.c['externalanalyses'],
                self.c['externalanalyses'].outputs['state']['Outer'], self.c['forecast'])

    # Extended forecasts (the sample set) start from the same cycled state.
    #
    # The mean IC is passed explicitly. ExtendedForecast otherwise defaults it
    # to the DA mean analysis in CyclingDA/<cycle>/an/mean and schedules a
    # MeanAnalysis task to build it -- neither of which exists here, because
    # there is no DA. At nMembers == 1 that failure is silent in a way worth
    # naming: MeanAnalysis.csh takes its deterministic pass-through branch,
    # symlinks an analysis that was never written, and exits 0. The extended
    # forecast then aborts under MPI on a dangling IC with no hint of the
    # cause.
    #
    # With one member the cycled forecast IS the mean, so warmIC[0] is exactly
    # the right state. With more than one, a real mean over the CyclingFC
    # members would have to be computed first, and MeanAnalysis.csh cannot do
    # it -- it reads CyclingDAOutDirs/ANFilePrefix straight from the DA config.
    assert members.n == 1 or not conf.has('extendedforecast.meanTimes'), \
      'ForecastOnlyCycle: extendedforecast.meanTimes needs a mean IC, and ' \
      'computing one over CyclingFC members is not implemented; run with ' \
      'members.n == 1 or drop meanTimes'
    meanIC = warmIC[0] if members.n == 1 else None
    self.c['extendedforecast'] = ExtendedForecast(conf, self.c['hpc'], members,
                self.c['forecast'], self.c['externalanalyses'], self.c['observations'],
                warmIC, 'internal', meanIC)

    # Bound the extended-forecast recurrence away from the first cycle point.
    #
    # The extended forecast launches from CyclingFC/{{prevCycleDate}}, the same
    # cycled state the 6-h forecast uses. At the first cycle point that
    # directory does not exist: cycle 1 runs ColdForecast/FirstBackground from
    # the external analysis and writes the state valid at cycle 2. Cylc
    # schedules an extended forecast at cycle 1 regardless -- a dependency on a
    # task instance with no definition at that point is dropped, not held -- and
    # the forecast then aborts under MPI on an IC that was never written.
    #
    # LIMITATION: this is unconditional, including for a restart run. When
    # 'restart cycle point' is later than the first cycle point, Workflow starts
    # ForecastTimes at the restart point and CyclingFC/{{prevCycleDate}} does
    # exist there, so the first extended forecast would be valid and is dropped
    # anyway -- costing one sample, or the only one for a single-point restart.
    # Making the exclusion conditional on not-a-restart is the right fix and needs
    # the restart semantics checked against Workflow first.
    #
    # Expressed as a cylc exclusion rather than an offset start: '^' is the
    # initial cycle point, so 'T00!^' keeps the daily-at-00Z meaning intact and
    # drops only the one point that cannot work. An offset start ('+PT6H/T00')
    # does not parse, and rewriting the recurrence as a period ('R/^+P1D/P1D')
    # would silently ignore the requested time of day whenever it differs from
    # the initial cycle point's.
    #
    # Consequence for planning: on a 31-day window at T00 this yields 30
    # samples, the first on day 2.
    for key in ['meanTimes', 'ensTimes']:
      times = conf.get('extendedforecast.'+key)
      if times is not None and '!' not in times:
        self.c['extendedforecast']._set(key, times+'!^')

    meshTitle = 'O'+meshes['Outer'].name
    if meshes['Inner'].name != meshes['Outer'].name:
      meshTitle += 'I'+meshes['Inner'].name
    defaultTitle = 'fconly_'+meshTitle

    self.c['experiment'] = Experiment(conf, self.c['hpc'], defaultTitle)
    self.c['ss'] = InvariantStream(conf, meshes, self.c['workflow']['FirstCycleDate'],
                self.c['externalanalyses'], self.c['experiment'])
    self.c['naming'] = Naming(conf, self.c['experiment'])

    # Per-lead-time external analyses, observations and chemistry ICs exist so
    # the extended forecast can be verified against analyses/obs valid at each
    # lead time. With no post-processing requested they are pure overhead, and
    # the analysis ones cannot succeed: GetGFSAnalysisFromRDA/UngribExternalAnalysis
    # fetch only the analysis at the cycle time, so ExternalAnalysisToMPAS-<N>hr
    # has no source for N > 0 and init_atmosphere aborts. Fall back to the
    # components' own default of [0] -- just the analysis time.
    ef = self.c['extendedforecast']
    # 'post' alone is not enough: it defaults NON-empty, while ExtendedForecast
    # emits verification tasks only when meanTimes (or a usable ensTimes) is set.
    # Gating on post alone therefore still expanded extLengths -- and recreated the
    # per-lead conversions that cannot succeed -- for the common case of a scenario
    # that leaves both defaults alone. Require post AND something actually
    # scheduled to verify.
    verifies = bool(ef['post']) and (ef['meanTimes'] is not None or ef['ensTimes'] is not None)
    icOffsets = ef['extLengths'] if verifies else [0]

    for k, c_ in self.c.items():
      if k in ['observations', 'initic', 'externalanalyses']:
        c_.export(icOffsets)
      elif k in ['forecast']:
        # No DA: wait on the PREVIOUS cycle's forecast instead of this cycle's
        # analysis. daMeanDir is unused here -- there is no mean background --
        # so point it at the forecast's own work directory rather than invent one.
        c_.export(None, Forecast.workDir+'/{{prevCycleDate}}',
                  previousStateDependency=self.c['forecast'].previousForecast)
      elif k in ['extendedforecast']:
        c_.export(self.c['forecast'].tf.finished)
      else:
        c_.export()

    self.queueComponents += [
      'externalanalyses',
      'initic',
      'observations',
    ]

    self.dependencyComponents += [
      'firstbackground',
      'forecast',
      'extendedforecast',
      # InitIC contributes the PrepareChemIC => ExternalAnalysisToMPAS and
      # PrepareEmissions => ExternalAnalysisToMPAS edges. Without it those tasks
      # are emitted under [runtime] but never referenced by the graph, so cylc
      # never runs them and ExternalAnalysisToMPAS.csh aborts with
      # "chemistry intermediate directory not found: .../ChemIC/<date>".
      'initic',
    ]

    self.taskComponents += [
      'firstbackground',
      'forecast',
      'extendedforecast',
      'externalanalyses',
      'initic',
      'observations',
      'emissions',
    ]
