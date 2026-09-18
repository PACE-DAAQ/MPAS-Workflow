#!/bin/csh -f
# Prepare raw-source MERRA2/GMI chemistry intermediates for init_atmosphere.
set ArgDT = "$1"
source config/environmentEmissions.csh
source config/auto/experiment.csh
source config/auto/initic.csh
# Generated only by the Cycle suite; sourcing a missing file is fatal in csh
# and exits before ./FAIL can be written.
if ( -e config/auto/emissions.csh ) source config/auto/emissions.csh
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
  echo "ERROR ${0}: initic chemistry source config is empty" > ./FAIL
  exit 1
endif

set WorkDir = ${ExperimentDirectory}/`echo "${initicChemistryWorkDir}" | sed 's@{{thisValidDate}}@'${thisValidDate}'@'`
mkdir -p ${WorkDir}

# Reuse an already-prepared valid time.
#
# The chemistry IC for a given valid time is a pure function of that time and
# the source config -- it does not depend on the experiment. PREPARE_CHEM_SUCCESS
# was already written on completion but never read, so every experiment rebuilt
# every valid time from MERRA2. Two experiments over the same period duplicated
# the whole set.
#
# The marker is touched only after merra_chem exits 0, so an interrupted or
# failed run leaves no marker and is correctly rebuilt. Require the MERRA2
# intermediate file as well, so a stray marker beside a deleted or truncated
# product does not cause a silent skip.
#
# Set PREPARE_CHEM_FORCE to rebuild regardless, e.g. after changing the source
# config or the MERRA2 inputs for a date already prepared.
set forceChem = 0
if ( $?PREPARE_CHEM_FORCE ) then
  set forceChem = 1
endif
if ( ${forceChem} == 0 && -e ${WorkDir}/PREPARE_CHEM_SUCCESS ) then
  set nMerra = `ls ${WorkDir}/MERRA2:* |& grep -vc '^ls:'`
  if ( ${nMerra} > 0 ) then
    echo "$0 (INFO): reusing chemistry IC already prepared for ${thisValidDate} in ${WorkDir}"
    exit 0
  endif
  echo "$0 (WARNING): ${WorkDir}/PREPARE_CHEM_SUCCESS exists but no MERRA2 intermediate is present; rebuilding"
endif

set py = "python3"
# tcsh expands the whole line before evaluating the condition, so a same-line
# "${emissionsPython}" raises "Undefined variable" even when $?emissionsPython
# is false. Suites without an Emissions component (ForecastFromExternalAnalyses)
# define no emissions.csh and so hit exactly that. Use a block form.
if ( $?emissionsPython ) then
  set py = "${emissionsPython}"
endif
# mainScriptDir is the installed experiment copy. Fall back to the tools/
# directory beside this script so the task can also be run directly from a
# checkout as a pre-flight check before submitting the suite.
set toolsDir = "${mainScriptDir}/tools"
if ( ! -d "$toolsDir" ) then
  set toolsDir = `cd $0:h/.. && pwd`/tools
endif
if ( ! -d "$toolsDir" ) then
  echo "ERROR ${0}: cannot locate the tools directory (tried ${mainScriptDir}/tools and $toolsDir)" > ./FAIL
  exit 1
endif
if ( $?PYTHONPATH ) then
  setenv PYTHONPATH "${toolsDir}:${PYTHONPATH}"
else
  setenv PYTHONPATH "${toolsDir}"
endif

set chem_cmd = "$py -m mpas_inputs.merra_chem ${initicChemistrySourceConfig} --valid ${thisValidDate} --output-dir ${WorkDir}"
if ( "${initicChemistryProcessorDirectory}" != "" ) then
  set chem_cmd = "${chem_cmd} --processor-dir ${initicChemistryProcessorDirectory}"
endif
echo "$0 (INFO): ${chem_cmd}"
eval ${chem_cmd}
if ( $status != 0 ) then
  echo "ERROR ${0}: chemistry source preparation failed" > ./FAIL
  exit 1
endif

touch ${WorkDir}/PREPARE_CHEM_SUCCESS
exit 0
