#!/bin/csh -f

# (C) Copyright 2023 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

# Process arguments
# =================
## args
# ArgDT: int, valid time offset beyond CYLC_TASK_CYCLE_POINT in hours
set ArgDT = "$1"

# ArgWorkDir: my location
set ArgWorkDir = "$2"

# ArgFilePrefix: prefix for output file
set ArgFilePrefix = "$3"

# ArgType: type of horizontal mesh 
set ArgType = "$4"

# ArgNCells: number of horizontal mesh cells
set ArgNCells = "$5"

# ArgRatio: Ratio of horizontal mesh cells
set ArgRatio = "$6"

# ArgExternalAnalysesDir: location of external analyses
set ArgExternalAnalysesDir = "$7"

set test = `echo $ArgDT | grep '^[0-9]*$'`
set isNotInt = ($status)
if ( $isNotInt ) then
  echo "ERROR in $0 : ArgDT must be an integer, not $ArgDT"
  exit 1
endif

date

# Setup environment
# =================
source config/environmentJEDI.csh
source config/auto/build.csh
source config/auto/experiment.csh
source config/auto/externalanalyses.csh
source config/auto/model.csh
source config/auto/invariantstream.csh
source config/auto/initic.csh
# Only the Cycle suite instantiates the Emissions component, so this file may not
# exist. Sourcing a missing file is fatal in csh and exits before ./FAIL is written.
if ( -e config/auto/emissions.csh ) source config/auto/emissions.csh
source config/tools.csh
set yymmdd = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 1-8`
set hh = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 10-11`
set thisCycleDate = ${yymmdd}${hh}
set thisValidDate = `$advanceCYMDH ${thisCycleDate} ${ArgDT}`
source ./bin/getCycleVars.csh

set WorkDir = ${ExperimentDirectory}/`echo "$ArgWorkDir" \
  | sed 's@{{thisValidDate}}@'${thisValidDate}'@' \
  `
set ExternalAnalysesDir = ${ExperimentDirectory}/`echo "$ArgExternalAnalysesDir" \
  | sed 's@{{thisValidDate}}@'${thisValidDate}'@' \
  `
echo "WorkDir = ${WorkDir}"
mkdir -p ${WorkDir}
cd ${WorkDir}

# ================================================================================================

# only need to continue if output file does not already exist
set outputFile = $ArgFilePrefix.$thisMPASFileDate.nc

if ( -e $outputFile ) then
  if ( -e CONVERTSUCCESS ) then
    echo "$0 (INFO): outputFile ($outputFile) and CONVERTSUCCESS file already exist, exiting with success"
    echo "$0 (INFO): if regenerating the output outputFile is desired, delete CONVERTSUCCESS"

    date

    exit 0
  endif

#  set oSize = `du -sh $outputFile | sed 's@'$outputFile'@@'`
#  if ( "$oSize" != "0" ) then
#    echo "$0 (INFO): outputFile ($outputFile) already exists, exiting with success"
#    echo "$0 (INFO): if regenerating the outputFile is desired, delete $outputFile"
#
#    date
#
#    exit 0
#  endif

  rm $outputFile
endif

# ================================================================================================
## link ungribbed files
ln -sfv ${ExternalAnalysesDir}/${externalanalyses__UngribPrefix}* ./

## link MPAS mesh graph info
rm ./x${ArgRatio}.${ArgNCells}.graph.info*
ln -sfv $GraphInfoDir/x${ArgRatio}.${ArgNCells}.graph.info* .

## Link MPAS invariant field
if ( $ArgType == "Outer" ) then
   ln -sfv $InvariantFieldsDirOuter/$InvariantFieldsFileOuter .
   set thisInvariantFile = $InvariantFieldsDirOuter/$InvariantFieldsFileOuter
else
   ln -sfv $InvariantFieldsDirInner/$InvariantFieldsFileInner .
   set thisInvariantFile = $InvariantFieldsDirInner/$InvariantFieldsFileInner
endif

## A GOCART2G-enabled init_atmosphere reads erod from the 'input' stream, because
## erod lives in the mesh var_struct of Registry_init_gocart2G.xml. An invariant file
## without it aborts deep inside MPAS with
##   nDustErosionDim *** not found in stream ***
## Only meshes whose invariant carries the GOCART2G static dust fields can be used for
## a chemistry cold start; fail here with an actionable message instead.
if ( $?initicChemistryMode ) then
  if ( "${initicChemistryMode}" != "off" ) then
    ncdump -h "${thisInvariantFile}" | grep -q 'nDustErosionDim'
    if ( $status != 0 ) then
      echo "ERROR ${0}: initic chemistry mode is '${initicChemistryMode}' but the invariant file has no GOCART2G static dust fields (nDustErosionDim/erod): ${thisInvariantFile}" > ./FAIL
      echo "  regenerate the invariant with a GOCART2G init_atmosphere, or set 'initic: chemistry mode: off'" >> ./FAIL
      exit 1
    endif
  endif
endif

## link lookup tables
foreach fileGlob ($MPASLookupFileGlobs)
  rm ./*${fileGlob}
  ln -sfv ${MPASLookupDir}/*${fileGlob} .
end

# MERRA chemistry intermediates separately and reuses the configured emission
# file families.  This cold-start conversion is shared across ensemble members;
# member-specific emission variants are selected later by Forecast.csh.
set initTemplate = ${StreamsFileInit}
set nmlTemplate = ${NamelistFileInit}
if ( "${initicChemistryMode}" != "off" ) then
  if ( "${initicChemistryMode}" == "workflow" ) then
    set chemDir = ${ExperimentDirectory}/`echo "${initicChemistryWorkDir}" | sed 's@{{thisValidDate}}@'${thisValidDate}'@'`
  else
    set chemDir = `echo "${initicChemistryPrebuiltDir}" | sed 's@{{thisValidDate}}@'${thisValidDate}'@'`
  endif
  if ( ! -d "${chemDir}" ) then
    echo "ERROR ${0}: chemistry intermediate directory not found: ${chemDir}" > ./FAIL
    exit 1
  endif
  ln -sfv ${chemDir}/MERRA2:* ./

  if ( "${initicChemistryBackgroundDir}" != "" ) then
    set chemBgDir = "${initicChemistryBackgroundDir}"
  else
    set chemBgDir = "${BackgroundLUTDir}"
  endif
  if ( ! -d "${chemBgDir}" ) then
    echo "ERROR ${0}: chemistry background directory not found: ${chemBgDir}" > ./FAIL
    exit 1
  endif
  ln -sfv ${chemBgDir}/* ./

  if ( "${initicEmissionMode}" == "workflow" ) then
    set initEmissionDir = "${ExperimentDirectory}/${initicEmissionWorkDir}"
  else
    set initEmissionDir = "${EmissionDir}"
  endif
  if ( ! -d "${initEmissionDir}" ) then
    echo "ERROR ${0}: emissions directory not found: ${initEmissionDir}" > ./FAIL
    exit 1
  endif
  ln -sfv ${initEmissionDir}/* ./
  # streams.init_atmosphere.gocart2g references {{prmArea}} in four prm_lowbc_*
  # input streams. bin/Forecast.csh links PRMAreaDir for the same reason; in
  # prebuilt emission mode the PRM file is not inside EmissionDir.
  if ( -d "${PRMAreaDir}" ) then
    ln -sfv ${PRMAreaDir}/* ./
  endif
  # Use the scenario-wide streams variant for the shared cold-start file.
  # Forecast.csh applies per-member memberVariants for the 9-member emissions ensemble.
  set saveStreamsFile = "${StreamsFile}"
  setenv StreamsFile ${StreamsFileInit}
  set nCells = ${ArgNCells}
  set meshRatio = ${ArgRatio}
  rm -f ./FAIL
  source ${mainScriptDir}/bin/SetStreamsVariant.csh
  setenv StreamsFile "${saveStreamsFile}"
  # 'exit' inside a sourced csh file does not terminate the sourcing script, so
  # SetStreamsVariant.csh's failures must be detected through its ./FAIL sentinel.
  # Without this the placeholders stay unresolved and the task reports success.
  if ( -e ./FAIL ) then
    echo "ERROR ${0}: SetStreamsVariant.csh failed for the cold-start streams file"
    exit 1
  endif
  set initTemplate = ${StreamsFileInit}.gocart2g
  set nmlTemplate = ${NamelistFileInit}.gocart2g
endif

## copy/modify dynamic streams file
rm ${StreamsFileInit}
cp -v $ModelConfigDir/initic/${StreamsFileInit} .
sed -i 's@{{nCells}}@'${ArgNCells}'@' ${StreamsFileInit}
sed -i 's@{{PRECISION}}@'${model__precision}'@' ${StreamsFileInit}
sed -i 's@{{meshRatio}}@'${ArgRatio}'@' ${StreamsFileInit}

## copy/modify dynamic namelist
rm ${NamelistFileInit}
cp -v $ModelConfigDir/initic/${NamelistFileInit} .
sed -i 's@startTime@'${thisMPASNamelistDate}'@' $NamelistFileInit
sed -i 's@nCells@'${ArgNCells}'@' $NamelistFileInit
sed -i 's@{{meshRatio}}@'${ArgRatio}'@' $NamelistFileInit
sed -i 's@{{UngribPrefix}}@'${externalanalyses__UngribPrefix}'@' $NamelistFileInit

# Run the executable
# ==================
rm ./${InitEXE}
ln -sfv ${InitBuildDir}/${InitEXE} ./
mpiexec ./${InitEXE}

# Check status
# ============
grep "Finished running the init_${MPASCore} core" log.init_${MPASCore}.0000.out
if ( $status != 0 ) then
  rm $outputFile
  echo "ERROR in $0 : MPAS-init failed" > ./FAIL
  exit 1
endif

date

touch CONVERTSUCCESS

exit 0
