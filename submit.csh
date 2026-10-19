#!/bin/csh

# (C) Copyright 2023 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

####################################################################################################
# This script runs a pre-generated cylc suite.rc file in the local directory. If the user
# has previously executed this script with the same "SuiteName", and the scenario is already
# running, then executing this script will automatically kill that running suite.
####################################################################################################

set workflow_dir="MPAS-Workflow"

# set up how much to log
if (! $?CYLC_DEBUG) then
   set CYLC_DEBUG=1
endif

if ( $CYLC_DEBUG > 1) then
  echo "$0 (INFO): Generating the scenario-specific ${workflow_dir} directory"
endif

# Create/copy the task shell scripts

# experiment provides mainScriptDir, SuiteName
source config/auto/experiment.csh

## Change to the cylc suite directory
if ( $CYLC_DEBUG > 1) then
  echo $0 cd ${mainScriptDir}
endif
cd ${mainScriptDir}

# Refuse to install a suite that is not self-contained. A suite staged by copying another suite's
# tree can keep the source suite's origin, cycle points, observers or plugs; every one of those
# faults runs happily and produces wrong output. Checked here rather than by hand, because the
# faults are invisible once the suite is playing.
# Exit 1 means the suite is faulty; exit 2 means the check itself could not run, which is only a
# warning. Set skipSuiteIndependenceCheck to install anyway.
if ( ! $?skipSuiteIndependenceCheck ) then
  if ( $CYLC_DEBUG > 1 ) then
    echo "$0 (INFO): checking that the staged suite is self-contained"
  endif
  python3 ${mainScriptDir}/tools/check_suite_independence.py ${mainScriptDir}/..
  set checkStatus = $status
  if ( $checkStatus == 1 ) then
    echo "$0 (FATAL): the staged suite failed check_suite_independence.py (see above)."
    echo "$0 (FATAL): not installing. Fix the suite, or set skipSuiteIndependenceCheck to override."
    exit 1
  else if ( $checkStatus != 0 ) then
    echo "$0 (WARNING): check_suite_independence.py could not run (status $checkStatus); continuing."
  endif
endif

set NCARHOST=$NCAR_HOST
if ( "$NCARHOST" == "derecho" ) then
  if ( ! $?CYLC_ENV ) then
    echo 'CYLC_ENV environment variable is not set, setting it to /glade/work/jwittig/conda-envs/my-cylc8.2'
    setenv CYLC_ENV /glade/work/jwittig/conda-envs/my-cylc8.2
  endif

  if ( $CYLC_DEBUG > 1) then
    echo $0 conda activate $CYLC_ENV
  endif
  conda activate $CYLC_ENV >& /dev/null
  #set cylc_timeout="--comms-timeout=10"
else if ( "$NCARHOST" == "cheyenne" ) then
  module purge
  echo $0 module load cylc
  module load cylc
  module load graphviz
else
  echo "unknown NCARHOST:" $NCARHOST
  exit 1
endif

echo $0 cylc version: `cylc --version`

echo "$0 (INFO): checking if a suite with the same name is already running"

if ( "$NCARHOST" == "derecho" ) then
  set status = `cylc scan -t rich | grep -c "${SuiteName} "`
  if ( $status > 0 ) then
    echo "$0 (INFO): a cylc suite named $SuiteName is already running!"
    echo "$0 (INFO): stopping the suite (30 sec.), then starting a new one..."
    echo $0 cylc stop --kill ${workflow_dir}/$SuiteName
    cylc stop --kill ${workflow_dir}/$SuiteName
    sleep 5
  else
    if ( $CYLC_DEBUG > 1) then
      echo "$0 (INFO): confirmed that a cylc suite named $SuiteName is not already running"
      echo "$0 (INFO): starting a new suite..."
    endif
  endif
else if ( "$NCARHOST" == "cheyenne" ) then
  echo $0 cylc poll $SuiteName 
  cylc poll $SuiteName 
  if ( $status == 0 ) then
    echo "$0 (INFO): a cylc suite named $SuiteName is already running!"
    echo "$0 (INFO): stopping the suite (30 sec.), then starting a new one..."
    cylc stop --kill $SuiteName
    sleep 30
  else
    echo "$0 (INFO): confirmed that a cylc suite named $SuiteName is not already running"
    echo "$0 (INFO): starting a new suite..."
  endif
endif

if ( $CYLC_DEBUG > 1) then
  echo $0 cylcWorkDir  ${cylcWorkDir}
  echo $0 SuiteName ${SuiteName}
  echo $0 mainScriptDir ${mainScriptDir}
endif
rm -rf ${cylcWorkDir}/${SuiteName}

if ( "$NCARHOST" == "derecho" ) then
  if ( -e ~/cylc-run/${workflow_dir}/${SuiteName} ) then
    if ( $CYLC_DEBUG > 1) then
      echo $0 cylc clean ${workflow_dir}/${SuiteName}
    endif
    cylc clean ${workflow_dir}/${SuiteName} >& /dev/null
  #  echo "Already has this suite, replay it"
  endif
  echo $0 cylc install --run-name=${SuiteName}
  cylc install --run-name=${SuiteName} >& /dev/null
  if ( $CYLC_DEBUG > 1) then
    echo $0  cylc validate ${workflow_dir}/${SuiteName}
  endif
  cylc validate ${workflow_dir}/${SuiteName}
  if ( $CYLC_DEBUG > 1) then
    echo $0 cylc play ${workflow_dir}/${SuiteName}
  endif
  cylc play ${workflow_dir}/${SuiteName}
else if ( "$NCARHOST" == "cheyenne" ) then
  echo "$0 (INFO): register, validate, and run the suite:", ${SuiteName}
  echo $0 cylc register ${SuiteName} ${mainScriptDir}
  cylc register ${SuiteName} ${mainScriptDir}
  echo $0  cylc validate --strict ${SuiteName}
  cylc validate --strict ${SuiteName}
  echo $0 cylc run ${SuiteName}
  cylc run ${SuiteName}
endif

cd -

exit 0
