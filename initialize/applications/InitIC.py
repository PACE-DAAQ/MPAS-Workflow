#!/usr/bin/env python3

'''
 (C) Copyright 2023 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''

from initialize.config.Component import Component
from initialize.config.Config import Config
from initialize.config.Resource import Resource
from initialize.config.Task import TaskLookup

from initialize.data.StateEnsemble import StateEnsemble
from initialize.data.ExternalAnalyses import ExternalAnalyses
from initialize.data.Emissions import Emissions

from initialize.framework.HPC import HPC

class InitIC(Component):
  defaults = 'scenarios/defaults/initic.yaml'
  variablesWithDefaults = {
    'chemistry mode': ['off', str, ['off', 'prebuilt', 'workflow']],
    'chemistry source config': ['', str],
    'chemistry processor directory': ['', str],
    'chemistry work directory': ['ChemIC/{{thisValidDate}}', str],
    'chemistry prebuilt directory': ['', str],
    'chemistry background directory': ['', str],
  }

  def __init__(self, config:Config, hpc:HPC, meshes:dict, ea:ExternalAnalyses, emissions:Emissions=None):
    super().__init__(config)

    self.ea = ea
    self.emissions = emissions
    self.meshes = meshes
    self._set('initicChemistryMode', self['chemistry mode'])
    self._set('initicChemistrySourceConfig', self['chemistry source config'])
    self._set('initicChemistryProcessorDirectory', self['chemistry processor directory'])
    self._set('initicChemistryWorkDir', self['chemistry work directory'])
    self._set('initicChemistryPrebuiltDir', self['chemistry prebuilt directory'])
    self._set('initicChemistryBackgroundDir', self['chemistry background directory'])
    self._set('initicEmissionMode', emissions['mode'] if emissions is not None else 'prebuilt')
    self._set('initicEmissionWorkDir', emissions['EmissionsWorkDir'] if emissions is not None else '')
    self._cshVars = list(self._vtable.keys())
    self.baseTask = 'ExternalAnalysisToMPAS'
    self.__used = self.baseTask in ea['PrepareExternalAnalysisOuter']

    ########################
    # tasks and dependencies
    ########################
    # job settings
    attr = {
      'retry': {'typ': str},
      'seconds': {'typ': int},
      'nodes': {'typ': int},
      'PEPerNode': {'typ': int},
      'queue': {'def': hpc['CriticalQueue']},
      'account': {'def': hpc['CriticalAccount']},
      'job_priority': {'def': hpc['CriticalPriority']},
    }
    job = Resource(self._conf, attr, ('job', meshes['Outer'].name))
    self.__task = TaskLookup[hpc.system](job)

    self.tf.group = ea.tf.group

    #########
    # outputs
    #########
    self.outputs = {}
    self.outputs['state'] = {}
    for typ, mesh in meshes.items():
      self.outputs['state'][typ] = StateEnsemble(mesh)
      self.outputs['state'][typ].append({
        'directory': ea['ExternalAnalysesDir'+typ],
        'prefix': ea['externalanalyses__filePrefix'+typ],
      })

  def export(self, dtOffsets:list=[0]):
    subqueues = []
    if self.__used:
      # only once for each mesh
      meshTypes = []
      meshNames = []
      meshNCells = []
      meshRatios = []
      for typ, mesh in self.meshes.items():
        if mesh.name not in meshNames:
          meshTypes.append(typ)
          meshNames.append(mesh.name)
          meshNCells.append(mesh.nCells)
          meshRatios.append(mesh.meshRatio)

      zeroHR = '-0hr'
      queue = 'ConvertExternalAnalyses'
      subqueues.append(queue)
      chemistryTasks = {}
      if self['chemistry mode'] == 'workflow':
        for dt in dtOffsets:
          dtStr = str(dt)
          chemTask = 'PrepareChemIC-'+dtStr+'hr'
          chemistryTasks[dt] = chemTask
          self._tasks += ['''
  [['''+chemTask+''']]
    inherit = '''+queue+''', '''+self.tf.execute+''', BATCH
    script = $origin/bin/PrepareChemIC.csh "'''+dtStr+'''"
'''+self.__task.job()+self.__task.directives()+'''
    [[[events]]]
      submission timeout = PT10M''']

      for (typ, meshName, nCells, meshRatio) in zip(meshTypes, meshNames, meshNCells, meshRatios):
        prevTaskName = None
        for dt in dtOffsets:
          dtStr = str(dt)
          args = [
            dt,
            self.ea['ExternalAnalysesDir'+typ],
            self.ea['externalanalyses__filePrefix'+typ],
            typ,
            nCells,
            meshRatio,
            self.ea.WorkDir,
          ]
          initArgs = ' '.join(['"'+str(a)+'"' for a in args])
          taskName = self.baseTask+'-'+meshName+'-'+dtStr+'hr'

          self._tasks += ['''
  [['''+taskName+''']]
    inherit = '''+queue+''', '''+self.tf.execute+''', BATCH
    script = $origin/bin/ExternalAnalysisToMPAS.csh '''+initArgs+'''
'''+self.__task.job()+self.__task.directives()+'''
    [[[events]]]
      submission timeout = PT10M''']

          # Source-first chemistry input must exist before init_atmosphere reads it.
          if self['chemistry mode'] == 'workflow':
            self._dependencies += ['''
    '''+chemistryTasks[dt]+''' => '''+taskName]
          if self['chemistry mode'] != 'off' and self.emissions is not None and self.emissions['mode'] == 'workflow':
            self._dependencies += ['''
    PrepareEmissions => '''+taskName]

          # make task[t+dt] depend on task[t]
          if prevTaskName is not None:
            # special catch-all succeed string needed due to 0hr naming below
            if dtOffsets[0] == 0 and dtOffsets.index(dt) == 1:
              success = ':succeed-all'
            else:
              success = ''

            self._dependencies += ['''
    '''+prevTaskName+success+''' => '''+taskName]

          prevTaskName = taskName

        # generic 0hr task names for external classes/tasks to grab
        self._tasks += ['''
  [['''+self.baseTask+'''-'''+meshName+''']]
    inherit = '''+self.baseTask+'''-'''+meshName+zeroHR]

    # only 1 task per subqueue to avoid cross-cycle errors
    for queue in set(subqueues):
      self._tasks += ['''
  [['''+queue+''']]
    inherit = '''+self.tf.group]

      self._queues += ['''
    [[['''+queue+''']]]
      members = '''+queue+'''
      limit = 1''']

    ###########################
    # update tasks/dependencies
    ###########################
    self._dependencies = self.tf.updateDependencies(self._dependencies)
    self._tasks = self.tf.updateTasks(self._tasks, self._dependencies)

    # export all
    super().export()
