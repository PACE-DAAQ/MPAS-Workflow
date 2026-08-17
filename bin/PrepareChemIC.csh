#!/bin/csh -f
# Prepare raw-source MERRA2/GMI chemistry intermediates for init_atmosphere.
set ArgDT = "$1"
source config/environmentEmissions.csh
source config/auto/experiment.csh
source config/auto/initic.csh
source config/auto/emissions.csh
source config/tools.csh
set yymmdd = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 1-8`
set hh = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 10-11`
set thisCycleDate = ${yymmdd}${hh}
set thisValidDate = `$advanceCYMDH ${thisCycleDate} ${ArgDT}`

if ( "${initicChemistryMode}" != "workflow" ) then
  echo "$0 (INFO): chemistry mode=${initicChemistryMode}; nothing to prepare"
  exit 0
endif
if ( "${initicChemistrySourceConfig}" == "" ) then
  echo "ERROR $0: initic chemistry source config is empty" > ./FAIL
  exit 1
endif

set WorkDir = ${ExperimentDirectory}/`echo "${initicChemistryWorkDir}" | sed 's@{{thisValidDate}}@'${thisValidDate}'@'`
mkdir -p ${WorkDir}
set py = "python3"
if ( $?emissionsPython ) set py = "${emissionsPython}"
if ( $?PYTHONPATH ) then
  setenv PYTHONPATH "${mainScriptDir}/tools:${PYTHONPATH}"
else
  setenv PYTHONPATH "${mainScriptDir}/tools"
endif

set chem_cmd = "$py -m mpas_inputs.merra_chem ${initicChemistrySourceConfig} --valid ${thisValidDate} --output-dir ${WorkDir}"
if ( "${initicChemistryProcessorDirectory}" != "" ) then
  set chem_cmd = "${chem_cmd} --processor-dir ${initicChemistryProcessorDirectory}"
endif
echo "$0 (INFO): ${chem_cmd}"
eval ${chem_cmd}
if ( $status != 0 ) then
  echo "ERROR $0: chemistry source preparation failed" > ./FAIL
  exit 1
endif

touch ${WorkDir}/PREPARE_CHEM_SUCCESS
exit 0
