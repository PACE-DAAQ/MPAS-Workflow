#!/usr/bin/env python3

'''
 (C) Copyright 2025 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''

from initialize.applications.Members import Members

from initialize.config.Component import Component
from initialize.config.Config import Config
from initialize.config.Resource import Resource
from initialize.config.Task import TaskLookup

from initialize.data.ExternalAnalyses import ExternalAnalyses
from initialize.data.Model import Model, Mesh

from initialize.framework.HPC import HPC
from initialize.framework.Workflow import Workflow


class EnsembleForecast(Component):
  '''
  Cycled emission-perturbed ensemble that is recentered every cycle onto the
  deterministic (members.n == 1) analysis.  See ensemble_recentering_plan.md.

  Component hosts:
    - RecenterEnsemble : 1 task (bin/RecenterEnsemble.csh), recenters the
      previous-cycle ensemble forecasts onto this cycle's deterministic analysis.
    - EnsembleForecast{mm} : nEnsFCMembers member forecast tasks that re-use
      bin/Forecast.csh as-is (12 positional args, mirroring Forecast.py).

  The ensemble size (self['n'] -> nEnsFCMembers) is INDEPENDENT of members.n.
  members.n stays 1 so the deterministic DA path is untouched.
  '''
  defaults = 'scenarios/defaults/ensembleforecast.yaml'
  workDir = 'CyclingEnsFC'
  forecastPrefix = 'mpasout'   # matches Forecast.forecastPrefix
  icPrefix = 'an'
  icSubDir = 'ic'
  fmt = '/mem{:03d}'

  variablesWithDefaults = {
    ## n: ensemble size, INDEPENDENT of members.n (which stays 1)
    'n': [0, int],

    ## updateSea
    # whether to update surface fields before a forecast (e.g., sst, xice)
    'updateSea': [True, bool],

    ## updateATMVarsFromCold
    # whether to update the IC atmospheric variables from the cold-start IC
    'updateATMVarsFromCold': [True, bool],

    ## post
    # list of tasks for Post (not yet wired for the ensemble stream)
    'post': [[], list],
  }

  def __init__(self,
    config:Config,
    hpc:HPC,
    mesh:Mesh,
    members:Members,
    model:Model,
    workflow:Workflow,
    ea:ExternalAnalyses,
  ):
    super().__init__(config)
    self.hpc = hpc
    self.mesh = mesh
    self.model = model
    self.workflow = workflow
    self.ea = ea

    ###################
    # derived variables
    ###################
    self.NN = self['n']
    self.memFmt = self.fmt if self.NN > 1 else ''
    self.active = (self.NN > 1 and self['execute'])

    # Export the ensemble member count under a DISTINCT name.  Component.varToCsh
    # would emit a bare `setenv n` (collides with other csh usage), so follow
    # Members.py and export `nEnsFCMembers`.  Exported unconditionally (even when
    # inactive, with value 0) so bin/getCycleVars.csh always has a safe default.
    self._set('nEnsFCMembers', self.NN)
    self._cshVars = ['nEnsFCMembers']

    # First-cycle (R1) staging resource (plan §8): a pre-staged ensemble (e.g. GEFS
    # EnsFcst_Noah_wMERRA2) is linked into CyclingEnsFC/<FirstCycleDate>/memNNN so the
    # first RecenterEnsemble and the SELF.EnsFC hybrid B have input.  Mirrors
    # firstbackground for the deterministic stream.  These csh vars drive
    # bin/LinkEnsembleForecasts.csh; only used when first cycle == restart cycle.
    self.R1present = (workflow['first cycle point'] == workflow['restart cycle point'])
    firstDir = self._conf.getOrDefault('first ensemble.directory', None, str)
    if firstDir is not None:
      firstDir = firstDir.replace('{{FirstCycleDate}}', workflow['FirstCycleDate'])
    self._set('firstensemble__directory', firstDir)
    self._set('firstensemble__filePrefix',
              self._conf.getOrDefault('first ensemble.filePrefix', 'mpasout', str))
    self._set('firstensemble__memberFormat',
              self._conf.getOrDefault('first ensemble.memberFormat', '/{:02d}', str))
    self._cshVars += ['firstensemble__directory', 'firstensemble__filePrefix',
                      'firstensemble__memberFormat']

    # WorkDir is where RecenterEnsemble is executed (arg to RecenterEnsemble.csh)
    self.WorkDir = self.workDir+'/{{thisCycleDate}}'

    # forecast length for the member forecasts (one cycling window)
    window = workflow['CyclingWindowHR']
    lengthHR = window
    outIntervalHR = window

    ########################
    # tasks and dependencies
    ########################
    if self.active:
      #########################################
      # RecenterEnsemble job (clone RTPP.py block)
      #########################################
      recAttr = {
        'retry': {'typ': str},
        'baseSeconds': {'typ': int},
        'secondsPerMember': {'typ': int},
        'nodes': {'typ': int},
        'PEPerNode': {'typ': int},
        'memory': {'def': '45GB', 'typ': str},
        'queue': {'def': hpc['CriticalQueue']},
        'account': {'def': hpc['CriticalAccount']},
        'job_priority': {'def': hpc['CriticalPriority']},
        'email': {'def': True, 'typ': bool},
      }
      recJob = Resource(self._conf, recAttr, ('job', 'recenterensemble'))
      recJob._set('seconds', recJob['baseSeconds'] + recJob['secondsPerMember'] * self.NN)
      recTask = TaskLookup[hpc.system](recJob)

      # RecenterEnsemble is an init-phase task: tf makes
      #   Init<base>:succeed-all => <base>Exec, i.e. RecenterEnsemble => member forecasts
      self._tasks += ['''
  [[RecenterEnsemble]]
    inherit = '''+self.tf.init+''', BATCH
    script = $origin/bin/RecenterEnsemble.csh "'''+self.WorkDir+'''"
'''+recTask.job()+recTask.directives()]

      #########################################
      # member forecast job (clone Forecast.py block)
      #########################################
      fcAttr = {
        'retry': {'typ': str},
        'baseSeconds': {'typ': int},
        'secondsPerForecastHR': {'typ': int},
        'nodes': {'typ': int},
        'PEPerNode': {'typ': int},
        'GPUPerNode': {'typ': int, 'req': False},
        'memory': {'def': '235GB', 'typ': str},
        'queue': {'def': hpc['CriticalQueue']},
        'account': {'def': hpc['CriticalAccount']},
        'job_priority': {'def': hpc['CriticalPriority']},
        'email': {'def': True, 'typ': bool},
      }
      fcJob = Resource(self._conf, fcAttr, ('job', mesh.name))
      fcJob._set('seconds', fcJob['baseSeconds'] + fcJob['secondsPerForecastHR'] * lengthHR)
      fcTask = TaskLookup[hpc.system](fcJob)

      # execute-phase family; individual member forecasts inherit it + BATCH
      self._tasks += ['''
  [[EnsembleForecasts]]
    inherit = '''+self.tf.execute+'''
'''+fcTask.job()+fcTask.directives()]

      for mm in range(1, self.NN+1, 1):
        # fcArgs mirrors Forecast.py:146-159 exactly (12 positional args).
        #   DACycling (True): IC is an analysis for which re-coupling is required
        #   DeleteZerothForecast (False): not used elsewhere in the workflow
        args = [
          mm,
          lengthHR,
          outIntervalHR,
          False,                                                          # IAU
          mesh.name,
          True,                                                           # DACycling
          False,                                                          # DeleteZerothForecast
          self['updateSea'],
          self.workDir+'/{{thisCycleDate}}'+self.memFmt.format(mm),       # WorkDir
          self.workDir+'/{{thisCycleDate}}/'+self.icSubDir+self.memFmt.format(mm),  # IC dir
          self.icPrefix,                                                  # IC prefix
          self['updateATMVarsFromCold'],
        ]
        fcArgs = ' '.join(['"'+str(a)+'"' for a in args])

        self._tasks += ['''
  [[EnsembleForecast'''+str(mm)+''']]
    inherit = EnsembleForecasts, BATCH
    script = $origin/bin/Forecast.csh '''+fcArgs]

  def export(self, daFinished:str, daPre:str):
    '''
    daFinished: the deterministic DA finished task; this cycle's analysis (the
                recentering center) is ready once it completes.
    daPre:      the deterministic DA family pre-task (e.g. PreDA__).  Gated on the
                ensemble forecast producer so the SELF.EnsFC hybrid B files exist
                before the deterministic Variational stages them (see below).
    '''
    if self.active:
      # open graph
      self._dependencies += ['''
    '''+self.workflow['AnalysisTimes']+''' = """''']

      # center ready this cycle (deterministic analysis) => RecenterEnsemble
      self.tf.addDependencies([daFinished])

      # previous-cycle ensemble forecasts (valid at T) ready => RecenterEnsemble
      # (offset idiom as in Forecast.py:167/226)
      prevEnsFC = self.tf.finished+'[-PT'+str(self.workflow['CyclingWindowHR'])+'H]'
      self.tf.addDependencies([prevEnsFC])

      # First-cycle seeding (plan §8): at the first AnalysisTimes cycle the
      # previous-cycle ensemble does NOT come from an EnsembleForecast (there is
      # none before R1) but from the R1 LinkEnsembleForecasts staging task.  Adding
      # LinkEnsembleForecasts[-PT{window}H] as a RecenterEnsemble prerequisite
      # enforces that ordering at the first cycle; at every later cycle the offset
      # points to a cycle where LinkEnsembleForecasts is absent, so cylc prunes it
      # (and the prevEnsFC dependency above takes over).  Only add when R1 exists.
      if self.R1present:
        linkPrev = 'LinkEnsembleForecasts[-PT'+str(self.workflow['CyclingWindowHR'])+'H]'
        self.tf.addDependencies([linkPrev])

      # RecenterEnsemble => EnsembleForecast{mm} is produced automatically by the
      # tf phase graph (Init<base>:succeed-all => <base>Exec).

      # NOTE (see REVIEW.md): member forecasts run bin/Forecast.csh with
      # updateSea=True; Forecast.py additionally makes them depend on
      # ea['PrepareSeaSurfaceUpdate'].  The plan §3 dependency list omits it, so it
      # is omitted here and flagged for human review.

      self._dependencies = self.tf.updateDependencies(self._dependencies)
      self._tasks = self.tf.updateTasks(self._tasks, self._dependencies)

      # close graph
      self._dependencies += ['''
      """''']

      # ----------------------------------------------------------------------
      # R1: stage the pre-staged ensemble into CyclingEnsFC/<FirstCycleDate>
      # (mirrors FirstBackground's LinkWarmStartBackgrounds).  Standalone quick
      # task with no upstream (the pre-staged files already exist on disk).
      # ----------------------------------------------------------------------
      if self.R1present:
        self._tasks += ['''
  [[LinkEnsembleForecasts]]
    inherit = SingleBatch
    script = $origin/bin/LinkEnsembleForecasts.csh
    execution time limit = PT120S
    execution retry delays = 1*PT10S''']

        self._dependencies += ['''
    R1 = """
        LinkEnsembleForecasts
      """''']

      # ----------------------------------------------------------------------
      # Flag #1: the deterministic hybrid B (SELF.EnsFC) at cycle T reads
      # CyclingEnsFC/(T-6), produced by EnsembleForecast at T-6 (or, for the first
      # AnalysisTimes cycle, by the R1 LinkEnsembleForecasts seed).  Make the DA
      # family wait for that producer so the B members exist before the Variational
      # (InitVariationals/PrepJEDI) stages them.  Emitted as raw edges into the DA
      # pre-task in a separate AnalysisTimes section (cylc unions edges per
      # recurrence).  The offset mirrors the recenter gating: at T-6 == R1 the
      # EnsembleForecast edge prunes and LinkEnsembleForecasts takes over; at later
      # cycles LinkEnsembleForecasts prunes and EnsembleForecast takes over.
      window = str(self.workflow['CyclingWindowHR'])
      bEdges = self.tf.finished+'[-PT'+window+'H] => '+daPre
      if self.R1present:
        bEdges += '''
        LinkEnsembleForecasts[-PT'''+window+'''H] => '''+daPre
      self._dependencies += ['''
    '''+self.workflow['AnalysisTimes']+''' = """
        # SELF.EnsFC hybrid B must be produced before the deterministic DA reads it
        '''+bEdges+'''
      """''']

    super().export()
