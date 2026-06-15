#!/usr/bin/env python3

'''
 (C) Copyright 2023 UCAR

 This software is licensed under the terms of the Apache Licence Version 2.0
 which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
'''
import os
from pathlib import Path

from initialize.config.Component import Component
from initialize.config.Config import Config

from initialize.data.Model import Model

class Build(Component):
  variablesWithDefaults = {
    ## mpas bundle
    # mpas-bundle build directory
    'mpas bundle': ['/replace/this/in/host/specific/code/below', str],

    # optional double-precision build
    #'mpas bundle': ['/glade/work/guerrett/pandac/build/mpas-bundle_gnu-openmpi_22MAR2023', str],

    # forecast directory
    # defaults to bundle build, otherwise specify full directory
    #'forecast directory': ['bundle', str],
    'forecast directory': ['/replace/this/in/host/specific/code/below/', str],

    ## bundle compiler used
    # {compiler}-{mpi-implementation}/{version} combination that selects the JEDI module used to build
    # the executables described herein
    'bundle compiler used': ['gnu-openmpi', str,
      ['gnu-openmpi', 'intel-impi']],

    ## non-bundle build directories
    # Ungrib (WPS)
    'wps build directory': ['', str],

    # Mean state calculator
    'meanstate build directory': ['', str],

    # Obs2IODA-v3
    'obs2ioda build directory':
      ['/glade/campaign/mmm/parc/ivette/pandac/codeBuild/obs2iodaV3/build/bin', str],

    # Update ATM vars
    'updateATMvars build directory': ['/glade/derecho/scratch/junpark/pandac', str],

    ## gocartMPAS data directories
    # Emission (resolution-dependent; default is 60km, monthly snapshot 202411)
    'gocart emission directory':
      ['/glade/campaign/ncar/nmmm0072/Data/MPAS-Workflow/gocart2g/emission_202411/60km', str],

    # Background LUT
    'gocart background lut directory':
      ['/glade/campaign/ncar/nmmm0072/Data/MPAS-Workflow/gocart2g/backgrounds', str],

    # Optics
    'gocart optics directory':
      ['/glade/campaign/ncar/nmmm0072/Data/MPAS-Workflow/gocart2g/optics', str],
  }

  def __init__(self, config:Config, model:Model=None):
    self.logPrefix = self.__class__.__name__+': '

    # set system dependent defaults before invoking Component ctor
    system = os.getenv('NCAR_HOST')
    if system == 'derecho':
      if config._bundle_dir != None:
        self.variablesWithDefaults['mpas bundle'] = [config._bundle_dir, str]
      else:
        self.variablesWithDefaults['mpas bundle'] = \
          ['/glade/derecho/scratch/jwittig/repos-s/mpas-bundle-cron/build-gnu-1p_latest', str] ## develop

      self.variablesWithDefaults['bundle compiler used'] = ['gnu-cray', str,
        ['gnu-cray', 'intel-cray']]
      self.variablesWithDefaults['forecast directory'] = ['bundle', str]

      # Ungrib
      self.variablesWithDefaults['wps build directory'] = \
        ['/glade/work/jwittig/repos1/WPS/', str]
      # Mean state calculator
      # FIXME the source for the app in this directory was copied from
      # /glade/work/guerrett/pandac/work/meanState/spack-stack_gcc-10.1.0_openmpi-4.1.1
      self.variablesWithDefaults['meanstate build directory'] = \
        ['/glade/campaign/mmm/parc/jwittig/meanState/bin', str]
    elif system == 'cheyenne':
      self.variablesWithDefaults['mpas bundle'] = \
        ['/glade/p/mmm/parc/liuz/pandac_common/mpas-bundle-code-build/mpas_bundle_2.0_gnuSP/build', str]
      self.variablesWithDefaults['bundle compiler used'] = ['gnu-openmpi', str,
        ['gnu-openmpi', 'intel-cray']]
      self.variablesWithDefaults['forecast directory'] = \
        ['/glade/p/mmm/parc/liuz/pandac_common/mpas-bundle-code-build/mpas_bundle_2.0_gnuSP/MPAS_intelmpt', str]

      # Ungrib
      self.variablesWithDefaults['wps build directory'] = \
        ['/glade/work/guerrett/pandac/data/GEFS', str]
      # Mean state calculator
      self.variablesWithDefaults['meanstate build directory'] = \
        ['/glade/work/guerrett/pandac/work/meanState/spack-stack_gcc-10.1.0_openmpi-4.1.1', str]
    else:
      self._msg('unknown host:' + system)

    super().__init__(config)

    ###################
    # derived variables
    ###################

    # MPAS-JEDI
    # ---------
    ## Variational
    self._set('VariationalEXE', 'mpasjedi_variational.x')
    self._set('VariationalBuildDir', self['mpas bundle']+'/bin')

    ## EnsembleOfVariational
    self._set('EnsembleOfVariationalEXE', 'mpasjedi_eda.x')
    self._set('EnsembleOfVariationalBuildDir', self['mpas bundle']+'/bin')

    ## EnKF
    self._set('EnKFEXE', 'mpasjedi_enkf.x')
    self._set('EnKFBuildDir', self['mpas bundle']+'/bin')

    ## HofX
    self._set('HofXEXE', 'mpasjedi_hofx3d.x')
    self._set('HofXBuildDir', self['mpas bundle']+'/bin')

    ## RTPP
    self._set('RTPPEXE', 'mpasjedi_rtpp.x')
    self._set('RTPPBuildDir', self['mpas bundle']+'/bin')

    ## RTPS
    self._set('RTPSEXE', 'mpasjedi_rtps.x')
    self._set('RTPSBuildDir', self['mpas bundle']+'/bin')

    ## SACA
    self._set('SACAEXE', 'mpasjedi_saca.x')
    self._set('SACABuildDir', self['mpas bundle']+'/bin')

    if model is not None:

      # MPAS-Model
      # ----------
      self._set('InitBuildDir', self['mpas bundle']+'/bin')
      self._set('InitEXE', 'mpas_init_'+model['MPASCore'])

      # either use forecast executable from the bundle or a separate MPAS-Atmosphere build
      self.log('self[mpas bundle] ' + self['mpas bundle'], level=self.MSG_DEBUG)
      self.log('self[forecast directory] ' + self['forecast directory'], level=self.MSG_DEBUG)
      if self['forecast directory'] == 'bundle':
        self._set('ForecastBuildDir', self['mpas bundle']+'/bin')
        self._set('ForecastEXE', 'mpas_'+model['MPASCore'])
      else:
        # look for atmosphere_model in the provided directory (original behavior)
        forecastDir = self['forecast directory']
        forecastExe = model['MPASCore']+'_model'
        self.log('looking for ' + forecastDir + '/' + forecastExe, level = self.MSG_DEBUG)
        if Path(forecastDir + '/' + forecastExe).is_file():
          self._set('ForecastBuildDir', forecastDir)
          self._set('ForecastEXE', forecastExe)
          self.log('Setting ForecastBuildDir to ' + forecastDir + ' , ForecastEXE to ' + forecastExe, level=self.MSG_QUIET)
        else:
          # if atmosphere_model wasn't found look for mpas_atmosphere
          forecastExe = 'mpas_'+model['MPASCore']
          self.log('looking for ' + forecastDir + '/' + forecastExe, level = self.MSG_DEBUG)
          if Path(forecastDir + '/' + forecastExe).is_file():
            self._set('ForecastBuildDir', forecastDir)
            self._set('ForecastEXE', forecastExe)
            self.log('Setting ForecastBuildDir to ' + forecastDir + ' , ForecastEXE to ' + forecastExe, level=self.MSG_QUIET)
          else:
            # if mpas_atmosphere wasn't found in the provided directory, look in bin
            forecastDir = forecastDir + '/bin'
            self.log('looking for ' + forecastDir + '/' + forecastExe, level = self.MSG_DEBUG)
            if Path(forecastDir + '/' + forecastExe).is_file():
              self._set('ForecastBuildDir', forecastDir)
              self._set('ForecastEXE', forecastExe)
              self.log('Setting ForecastBuildDir to ' + forecastDir + ' , ForecastEXE to ' + forecastExe, level=self.MSG_QUIET)
            else:
              self.log('could not find forecast executable in ' + self['forecast directory'], level=self.MSG_QUIET)

      if system == 'derecho':
        self._set('MPASLookupDir', self['mpas bundle']+'/MPAS/core_atmosphere')
      #  self._set('MPASLookupDir', self['forecast directory']) # need to obtain files from the directory of MPAS executable
        self._set('MPASLookupFileGlobs', ['.TBL', '.DBL', 'DATA', 'VERSION'])
      elif system == 'cheyenne':
        self._set('MPASLookupDir', self['mpas bundle']+'/MPAS/core_'+model['MPASCore'])
        self._set('MPASLookupFileGlobs', ['.TBL', '.DBL', 'DATA', 'COMPATABILITY', 'VERSION'])

      # Alternatively, use a stand-alone single-precision build of MPAS-A with GNU-MPT
      #self._set('ForecastBuildDir', '/glade/p/mmm/parc/liuz/pandac_common/20220309_mpas_bundle/code/MPAS-gnumpt-single')
      #self._set('ForecastEXE', model['MPASCore']+'_model')

      # Note: this also requires modifying bin/Forecast.csh:
      #-source config/environmentJEDI.csh
      #+source config/environmentMPT.csh
      #-  mpiexec ./${ForecastEXE}
      #-  #mpiexec_mpt ./${ForecastEXE}
      #+  #mpiexec ./${ForecastEXE}
      #+  mpiexec_mpt ./${ForecastEXE}


    # Non-bundle applications
    # =======================

    # Ungrib
    # ------
    self._set('ungribEXE', 'ungrib.exe')
    self._set('WPSBuildDir', self['wps build directory'])

    # Obs2IODA-v3
    # -----------
    self._set('obs2iodaEXE', 'obs2ioda_v3')
    self._set('obs2iodaBuildDir', self['obs2ioda build directory'])

    # Update MPASVars code
    # -----------
    self._set('CopyMPASVarEXE', 'copy_mpas_vars.py.org')
    self._set('CopyMPASVarBuildDir', self['updateATMvars build directory'])

    # Mean state calculator
    # ---------------------
    #self._set('meanStateExe', 'mpasjedi_ens_mean_variance.x')
    self._set('meanStateExe', 'average_netcdf_files_parallel_mpas.x')
    self._set('meanStateBuildDir', self['meanstate build directory'])
    self.log('self meanStateBuildDir ' + self['meanStateBuildDir'], level=self.MSG_DEBUG)

    # gocartMPAS (resolution-/time-dependent — override per scenario YAML if needed)
    self._set('EmissionDir', self['gocart emission directory'])
    self._set('BackgroundLUTDir', self['gocart background lut directory'])
    self._set('OpticsDir', self['gocart optics directory'])

    self._cshVars = list(self._vtable.keys())
    self.log('self._cshVars ' + str(self._cshVars), level=self.MSG_NOISY)
