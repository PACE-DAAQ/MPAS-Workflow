#!/bin/csh -f

# Python environment for workflow-native emission preprocessing.
#
# If MPAS_EMISSIONS_CONDA_ENV is set, activate that environment. This is the
# recommended route when automatic ESMF weight generation requires ESMPy.
# Otherwise use the existing MPAS-Workflow NPL environment, which is sufficient
# for mesh preparation, FINN mapping, and application of pre-existing weights.

if ( $?config_environmentEmissions ) exit 0
setenv config_environmentEmissions 1

if ( "$NCAR_HOST" == "derecho" ) then
  source /etc/profile.d/z00_modules.csh
  module purge
  module load conda/latest
  if ( $?MPAS_EMISSIONS_CONDA_ENV ) then
    conda activate "$MPAS_EMISSIONS_CONDA_ENV"
  else
    conda activate npl
  endif
  module list
else
  source config/environmentNPL.csh
endif
