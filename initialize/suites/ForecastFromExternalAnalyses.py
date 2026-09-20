#!/usr/bin/env python3

'''
 (C) Copyright 2023 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''

from initialize.applications.InitIC import InitIC
from initialize.applications.ExtendedForecast import ExtendedForecast
from initialize.applications.Forecast import Forecast
from initialize.applications.Members import Members

from initialize.config.Config import Config

from initialize.data.ExternalAnalyses import ExternalAnalyses
from initialize.data.Emissions import Emissions
from initialize.data.Model import Model
from initialize.data.Observations import Observations
from initialize.data.InvariantStream import InvariantStream

from initialize.framework.Build import Build
from initialize.framework.Experiment import Experiment
from initialize.framework.Naming import Naming

#from initialize.post.Benchmark import Benchmark

from initialize.suites.SuiteBase import SuiteBase

class ForecastFromExternalAnalyses(SuiteBase):
  def __init__(self, conf:Config):
    super().__init__(conf)

    self.c['model'] = Model(conf)
    meshes = self.c['model'].getMeshes()

    self.c['build'] = Build(conf, self.c['model'])
    self.c['emissions'] = Emissions(conf, self.c['hpc'], meshes['Outer'])
    self.c['observations'] = Observations(conf, self.c['hpc'])
    self.c['members'] = Members(conf)

    self.c['externalanalyses'] = ExternalAnalyses(conf, self.c['hpc'], meshes, self.c['members'])
    self.c['initic'] = InitIC(conf, self.c['hpc'], meshes, self.c['externalanalyses'], self.c['emissions'],
                self.c['workflow'])

    # Forecast object is only used to initialize parts of ExtendedForecast
    self.c['forecast'] = Forecast(conf, self.c['hpc'], meshes['Outer'], self.c['members'], self.c['model'], self.c['observations'],
                self.c['workflow'], self.c['externalanalyses'],
                self.c['externalanalyses'].outputs['state']['Outer'], self.c['emissions'])
    self.c['extendedforecast'] = ExtendedForecast(conf, self.c['hpc'], self.c['members'], self.c['forecast'],
                self.c['externalanalyses'], self.c['observations'],
                self.c['externalanalyses'].outputs['state']['Outer'], 'external')

    self.c['experiment'] = Experiment(conf, self.c['hpc'])
    self.c['ss'] = InvariantStream(conf, meshes, self.c['workflow']['FirstCycleDate'], self.c['externalanalyses'], self.c['experiment'])

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
      elif k in ['extendedforecast']:
        # Forecast.export is skipped in this suite. Gate extended forecasts
        # directly, including runs with chemistry initialization disabled.
        if self.c['emissions'].ready is not None:
          c_.tf.addDependencies([self.c['emissions'].ready])
        c_.export(self.c['externalanalyses']['PrepareExternalAnalysisOuter'])
      elif k in ['forecast']:
        continue
      else:
        c_.export()

    self.queueComponents += [
      'externalanalyses',
      'initic',
      'observations',
    ]

    self.dependencyComponents += [
      'extendedforecast',
      # InitIC contributes the PrepareChemIC => ExternalAnalysisToMPAS and
      # PrepareEmissions => ExternalAnalysisToMPAS edges. Without it those tasks
      # are emitted under [runtime] but never referenced by the graph, so cylc
      # never runs them and ExternalAnalysisToMPAS.csh aborts with
      # "chemistry intermediate directory not found: .../ChemIC/<date>".
      'initic',
    ]

    self.taskComponents += [
      'extendedforecast',
      'externalanalyses',
      'initic',
      'observations',
    ]

    if self.c['emissions'].ready is not None:
      self.taskComponents.append('emissions')
