#!/bin/csh -f

# (C) Copyright 2023 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

# Get GFS analysis (0-h forecast) for cold start initial conditions

# Process arguments
# =================
## args

# ArgDT: int, valid time offset beyond CYLC_TASK_CYCLE_POINT in hours
set ArgDT = "$1"

# ArgWorkDir: my location
set ArgWorkDir = "$2"

# ArgExternalDirectory: location of external files
set ArgExternalDirectory = "$3"

# ArgFilePrefix: common prefix for external/local files
set ArgFilePrefix = "$4"

# ArgNMembers: int, number of ensemble members (1 => single deterministic IC)
set ArgNMembers = "$5"

# ArgMemberFormat: python format string for the per-member subdirectory (e.g. "/{:02d}");
#                  ignored when ArgNMembers == 1
set ArgMemberFormat = "$6"

# ArgMaxMembers: int, maximum members available (memberDir wraps modulo this)
set ArgMaxMembers = "$7"

set test = `echo $ArgDT | grep '^[0-9]*$'`
set isNotInt = ($status)
if ( $isNotInt ) then
  echo "ERROR in $0 : ArgDT must be an integer, not $ArgDT"
  exit 1
endif

date

# Setup environment
# =================
source config/auto/build.csh
source config/auto/experiment.csh
source config/auto/externalanalyses.csh
source config/auto/model.csh
source config/tools.csh
set yymmdd = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 1-8`
set hh = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 10-11`
set thisCycleDate = ${yymmdd}${hh}
set thisValidDate = `$advanceCYMDH ${thisCycleDate} ${ArgDT}`
source ./bin/getCycleVars.csh

set WorkDir = "${ExperimentDirectory}/"`echo "$ArgWorkDir" \
  | sed 's@{{thisValidDate}}@'${thisValidDate}'@' \
  `
set directory = `echo "$ArgExternalDirectory" \
  | sed 's@{{thisValidDate}}@'${thisValidDate}'@' \
  `
echo "WorkDir = ${WorkDir}"
mkdir -p ${WorkDir}

# ================================================================================================
# Link the pre-staged IC for each ensemble member into its own subdirectory of WorkDir.
# For ArgNMembers == 1 memberDir returns an empty string, so this links
#   $directory/$thisValidDate/*.nc -> $WorkDir/   (identical to the original single-IC behavior).
# For ArgNMembers > 1 member NN is taken from $directory/$thisValidDate<memberSubdir>/ and linked
# into $WorkDir<memberSubdir>/, where <memberSubdir> comes from ArgMemberFormat (e.g. /01, /02).

@ member = 1
while ( $member <= $ArgNMembers )
  set memSub = `${memberDir} $ArgNMembers $member "${ArgMemberFormat}" -m ${ArgMaxMembers}`
  set srcDir = "${directory}/${thisValidDate}${memSub}"
  set dstDir = "${WorkDir}${memSub}"
  echo "member ${member}: linking ${srcDir} -> ${dstDir}"
  mkdir -p ${dstDir}
  ln -sfv ${srcDir}/*.$thisMPASFileDate.nc ${dstDir}/
  @ member++
end

date

exit 0
