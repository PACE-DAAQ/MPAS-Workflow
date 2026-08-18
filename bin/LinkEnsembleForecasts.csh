#!/bin/csh -f

# (C) Copyright 2025 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

####################################################################################################
# First-cycle (R1) seeding of the cycled ensemble.
#
# Links a pre-staged ensemble (e.g. the 60km GEFS EnsFcst_Noah_wMERRA2 archive) into
#   CyclingEnsFC/${FirstCycleDate}/memNNN/${FCFilePrefix}.${nextFirstFileDate}.nc
# so that at the next cycle (the first AnalysisTimes tick) both
#   - RecenterEnsemble        (reads prevCyclingEnsFCDirs), and
#   - the deterministic hybrid B (SELF.EnsFC -> CyclingEnsFC/{{prevDateTime}})
# have their ensemble input.  This mirrors bin/LinkWarmStartBackgrounds.csh, which
# stages the deterministic first background for the same first cycle.
#
# Runs only at R1 (first cycle point == restart cycle point); the source layout is
#   ${firstensemble__directory}/<member>/${firstensemble__filePrefix}.${nextFirstFileDate}.nc
# with <member> formatted by ${firstensemble__memberFormat} (e.g. "/{:02d}" -> 01..NN).
# The source member numbering may differ from the workflow's 3-digit memNNN; this
# script bridges them by linking into CyclingEnsFCDirs (3-digit) from the 2-digit source.
####################################################################################################

source config/auto/ensembleforecast.csh
source config/auto/members.csh
source config/auto/model.csh
source config/auto/workflow.csh

# stage into the FirstCycleDate ensemble directory
set thisCycleDate = $FirstCycleDate
set thisValidDate = $thisCycleDate
source ./bin/getCycleVars.csh

# nothing to seed unless this is an ensemble run
if ( ${nEnsFCMembers} < 2 ) then
  echo "$0 (INFO): nEnsFCMembers < 2, nothing to seed"
  exit 0
endif

if ( "$firstensemble__directory" == "None" || "$firstensemble__directory" == "" ) then
  echo "ERROR in $0 : ensembleforecast: first ensemble: directory is not set" > ./FAIL
  exit 1
endif

set member = 1
while ( $member <= ${nEnsFCMembers} )
  # (re)create the destination member directory
  if ( -d $CyclingEnsFCDirs[$member] ) rm -r $CyclingEnsFCDirs[$member]
  mkdir -p $CyclingEnsFCDirs[$member]

  # source member directory (2-digit source convention via memberFormat); the
  # leading "2" makes memberDir always apply the format (as in LinkWarmStartBackgrounds)
  set srcDir  = "$firstensemble__directory"`${memberDir} 2 $member "${firstensemble__memberFormat}"`
  set srcFile = ${srcDir}/${firstensemble__filePrefix}.${nextFirstFileDate}.nc

  if ( ! -e ${srcFile} ) then
    echo "ERROR in $0 : missing pre-staged ensemble file ${srcFile}" > ./FAIL
    exit 1
  endif

  # link into the workflow's 3-digit memNNN destination with the standard forecast name
  ln -sfv ${srcFile} $CyclingEnsFCDirs[$member]/${FCFilePrefix}.${nextFirstFileDate}.nc

  @ member++
end

exit 0
