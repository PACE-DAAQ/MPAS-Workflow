#!/usr/bin/env python3

'''
 (C) Copyright 2023 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''

from copy import deepcopy

from initialize.config.Component import Component
from initialize.config.Config import Config

class Mesh():
  def __init__(self, name, nCells, meshRatio, attrib=None):
    self.name = str(name)
    self.nCells = int(nCells)
    self.meshRatio = int(meshRatio)
    self.attrib = attrib

  def __eq__(self, other):
    return all([
      isinstance(other, Mesh),
      other.name == self.name,
      other.nCells == self.nCells,
      other.meshRatio == self.meshRatio,
      (other.attrib is None and self.attrib is None) or (other.attrib == self.attrib),
    ])


class Model(Component):
  defaults = 'scenarios/defaults/model.yaml'
  # mesh descriptors, e.g.:
  # uniform spacing: 30km, 60km, 120km
  # variable spacing: 60-3km

  requiredVariables = {
  }

  optionalVariables = {
    ## outerMesh [Required Parameter]
    # Variational outer loop, Forecast, HofX, verification
    'outerMesh': str,

    # TODO: specify these inner and ensemble meshes under da classes (variational, enkf, etc...)
    ## innerMesh [Optional, used in Variational]
    # variational inner loop
    'innerMesh': str,

    ## ensembleMesh [Optional, used in Variational]
    # variational ensemble, rtpp
    # note: mpas-jedi requires innerMesh and ensembleMesh to be equal at this time
    'ensembleMesh': str,
  }

  variablesWithDefaults = {
    ## GraphInfoDir
    # directory containing x{{meshRatio}}.{{nCells}}.graph.info* files
    #'GraphInfoDir': ['/glade/derecho/scratch/taosun/pandac/MPAS_GRAPH', str],
    'GraphInfoDir': ['/glade/campaign/ncar/nmmm0081/Data/MPAS-Workflow/pandac/MPAS_GRAPH', str],

    ## precision
    # floating-point precision of all application output
    # OPTIONS: single, double
    'precision': ['single', str],

    ## MPThompsonTablesDir
    # directory containing MP Thompson tables
    #'MPThompsonTablesDir': ['/glade/campaign/mmm/parc/ivette/pandac/saca/thompson_tables',str],
    'MPThompsonTablesDir': ['/glade/campaign/ncar/nmmm0081/Data/MPAS-Workflow/pandac/thompson_tables',str],

    ## streams variant
    # Selects a default GOCART emission-inventory combination (anthropogenic / biogenic /
    # biomass-burning) for the forecast streams.atmosphere. The variant -> combination mapping
    # lives in bin/SetStreamsVariant.csh; edit that switch to add or change combinations.
    # OPTIONS: cntl, pert01..pert08 (see bin/SetStreamsVariant.csh). Empty string behaves like cntl.
    # The shell switch is the authoritative list, so the value is intentionally not enum-validated
    # here -- variants added in the shell do not require a Python change.
    'streams variant': ['cntl', str],

    ## anth emissions / biob emissions / biog emissions
    # Optional per-dimension overrides of the 'streams variant' inventory combination. When set,
    # each overrides only its own dimension; when empty, the variant's default is used. The
    # inventory-name -> filename tables live in bin/SetStreamsVariant.csh.
    # OPTIONS: anth emissions: cams|ceds|cams-mix ; biob emissions: finn|gfas|qfed ; biog emissions: cams
    'anth emissions': ['', str],
    'biob emissions': ['', str],
    'biog emissions': ['', str],

    ## member variants
    # Optional per-ensemble-member emission variants. When non-empty, ensemble member NN (1-based,
    # from Forecast.csh $ArgMember) uses memberVariants[NN] instead of the scenario-wide 'streams
    # variant'; members beyond the list fall back to 'streams variant'. Entries are the same names
    # as 'streams variant' (cntl, pert01..pert08, ...). Empty => all members share 'streams variant'.
    # The selection is applied in bin/SetStreamsVariant.csh.
    'member variants': [[], list],

    ## PRM (plume rise model) namelist flags (&plumerisemodel in config/mpas/forecast/namelist.atmosphere)
    # Exported as doBburnPrm / doFrp and substituted into the namelist by bin/Forecast.csh.
    # 'do bburn prm': enable biomass-burning plume rise (config_do_bburnPRM).
    # 'do frp'      : use Fire Radiative Power instead of area-based emissions (config_do_FRP).
    'do bburn prm': [False, bool],
    'do frp': [False, bool],
  }

  def __init__(self, config:Config):
    super().__init__(config)

    ###################
    # derived variables
    ###################
    self._set('model__precision', self['precision'])

    TemplateFieldsPrefix = 'templateFields'
    self._set('TemplateFieldsPrefix', TemplateFieldsPrefix)

    localInvariantFieldsPrefix = 'invariant'
    self._set('localInvariantFieldsPrefix', localInvariantFieldsPrefix)

    MPASCore = 'atmosphere'
    self._set('MPASCore', MPASCore)

    StreamsFile = 'streams.'+MPASCore
    self._set('StreamsFile', StreamsFile)

    NamelistFile = 'namelist.'+MPASCore
    self._set('NamelistFile', NamelistFile)

    self._set('StreamsFileInit', 'streams.init_'+MPASCore)
    self._set('NamelistFileInit', 'namelist.init_'+MPASCore)
    self._set('NamelistFileWPS', 'namelist.wps')

    # The forecast streams.atmosphere is a single template; the emission-inventory combination is
    # selected at run time by bin/SetStreamsVariant.csh from 'streams variant' (exported as
    # streamsVariant) plus the optional anth/biob/biog emissions overrides.

    self.__meshes = {}
    for meshTyp in ['outer', 'inner', 'ensemble']:
      m = meshTyp+'Mesh'
      Typ = meshTyp.capitalize()

      name = self[m]
      if name is not None:
        self._set('nCells'+Typ, self._conf.getOrDie('resources.'+name+'.nCells'))
        nCells = self['nCells'+Typ]
        self._set('meshRatio'+Typ, self._conf.getOrDie('resources.'+name+'.meshRatio'))
        meshRatio = self['meshRatio'+Typ]

        self.__meshes[Typ] = Mesh(name, nCells, meshRatio)

        self._set('InitFilePrefix'+Typ, 'x'+str(meshRatio)+'.'+str(nCells)+'.init')
        self._set(meshTyp+'StreamsFile', StreamsFile+'_'+name)
        self._set(meshTyp+'NamelistFile', NamelistFile+'_'+name)
        self._set('TemplateFieldsFile'+Typ, TemplateFieldsPrefix+'.'+str(nCells)+'.nc')
        self._set('localInvariantFieldsFile'+Typ, localInvariantFieldsPrefix+'.'+str(nCells)+'.nc')

        self._set('TimeStep'+Typ, self._conf.getOrDie('resources.'+name+'.TimeStep'))
        self._set('DiffusionLengthScale'+Typ, self._conf.getOrDie('resources.'+name+'.DiffusionLengthScale'))
        self._set('RadiationLWInterval'+Typ, self._conf.getOrDie('resources.'+name+'.RadiationLWInterval'))
        self._set('RadiationSWInterval'+Typ, self._conf.getOrDie('resources.'+name+'.RadiationSWInterval'))
        self._set('PhysicsSuite'+Typ, self._conf.getOrDie('resources.'+name+'.PhysicsSuite'))
        self._set('Microphysics'+Typ, self._conf.getOrDie('resources.'+name+'.Microphysics'))
        self._set('Convection'+Typ, self._conf.getOrDie('resources.'+name+'.Convection'))
        self._set('PBL'+Typ, self._conf.getOrDie('resources.'+name+'.PBL'))
        self._set('Gwdo'+Typ, self._conf.getOrDie('resources.'+name+'.Gwdo'))
        self._set('RadiationCloud'+Typ, self._conf.getOrDie('resources.'+name+'.RadiationCloud'))
        self._set('RadiationLW'+Typ, self._conf.getOrDie('resources.'+name+'.RadiationLW'))
        self._set('RadiationSW'+Typ, self._conf.getOrDie('resources.'+name+'.RadiationSW'))
        self._set('SfcLayer'+Typ, self._conf.getOrDie('resources.'+name+'.SfcLayer'))
        self._set('LSM'+Typ, self._conf.getOrDie('resources.'+name+'.LSM'))

    self._cshVars = list(self._vtable.keys())

  def getMeshes(self):
    return deepcopy(self.__meshes)
