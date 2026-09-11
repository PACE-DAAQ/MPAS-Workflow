#!/bin/csh -f

# (C) Copyright 2025 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

# Recenter the previous-cycle emission-perturbed ensemble forecasts onto this
# cycle's deterministic (members.n == 1) analysis.
#   center   = CyclingDA/T/an/an.<T>.nc                         (deterministic analysis)
#   ensemble = CyclingEnsFC/(T-6)/memNNN/mpasout.<T>.nc         (prev ens forecasts, valid at T)
#   -> CyclingEnsFC/T/ic/memNNN/an.<T>.nc                       (recentered ICs)
# Cloned from bin/RTPP.csh (same class of job: read ensemble member dirs, sed a
# member list into a yaml, run an mpasjedi ensemble app on the Ensemble mesh).
# See ensemble_recentering_plan.md §4.

# Process arguments
# =================
## args
# ArgWorkDir: my location
set ArgWorkDir = "$1"

date

# Setup environment
# =================
source config/environmentJEDI.csh
source config/mpas/variables.csh
source config/tools.csh
source config/auto/build.csh
source config/auto/experiment.csh
source config/auto/members.csh
source config/auto/model.csh
source config/auto/invariantstream.csh
source config/auto/workflow.csh
source config/auto/ensembleforecast.csh
set yymmdd = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 1-8`
set hh = `echo ${CYLC_TASK_CYCLE_POINT} | cut -c 10-11`
set thisCycleDate = ${yymmdd}${hh}
set thisValidDate = ${thisCycleDate}
source ./bin/getCycleVars.csh

if (${nEnsFCMembers} < 2) then
  exit 0
endif

# app yaml (production template shaped from rtpp.yaml + the tested test yaml)
set appyaml = ens_recenter.yaml

# remove static work directory if it already exists
set self_WorkDir = "${ExperimentDirectory}/"`echo "$ArgWorkDir" \
  | sed 's@{{thisCycleDate}}@'${thisCycleDate}'@' \
  `
if ( -d $self_WorkDir ) then
  rm -r $self_WorkDir
endif

echo "WorkDir = ${self_WorkDir}"
mkdir -p ${self_WorkDir}
cd ${self_WorkDir}

# build, executable, yaml
set myBuildDir = ${EnsRecenterBuildDir}
set myEXE = ${EnsRecenterEXE}
set myYAML = ${self_WorkDir}/$appyaml

# other static variables
# center = this cycle's deterministic analysis (members.n == 1 => CyclingDAOutDir has no memN suffix)
set anPrefix = $ANFilePrefix
set centerFile = ${CyclingDAOutDir}/${anPrefix}.${thisMPASFileDate}.nc

# ensemble = previous-cycle ensemble forecasts, valid at this cycle time
set ensPrefix = $FCFilePrefix
set ensDirs = ($prevCyclingEnsFCDirs)

# recentered output directories (this cycle's ic/memNNN + ic/mean)
mkdir -p ${CyclingEnsFCICDir}/mean
set member = 1
while ( $member <= ${nEnsFCMembers} )
  mkdir -p $CyclingEnsFCICDirs[$member]
  @ member++
end

# Remove old logs
rm jedi.log*

# ================================================================================================

## copy invariant fields
rm ${localInvariantFieldsPrefix}*.nc
rm ${localInvariantFieldsPrefix}*.nc-lock
set localInvariantFieldsFile = ${localInvariantFieldsFileEnsemble}
rm ${localInvariantFieldsFile}
set InvariantFieldsFile = ${InvariantFieldsDirEnsemble}/${InvariantFieldsFileEnsemble}
ln -sfv ${InvariantFieldsFile} ${localInvariantFieldsFile}${OrigFileSuffix}
cp -v ${InvariantFieldsFile} ${localInvariantFieldsFile}

# ====================
# Model-specific files
# ====================
## link MPAS mesh graph info
ln -sfv $GraphInfoDir/x${meshRatioEnsemble}.${nCellsEnsemble}.graph.info* .

## link lookup tables
foreach fileGlob ($MPASLookupFileGlobs)
  ln -sfv ${MPASLookupDir}/*${fileGlob} .
end

if (${MicrophysicsOuter} == 'mp_thompson' ||${MicrophysicsOuter} == 'mp_thompson_gocart2G' ) then
  ln -svf $MPThompsonTablesDir/* .
endif

## link/copy stream_list/streams configs (reuse rtpp/ configs: same Ensemble-mesh geometry)
foreach staticfile ( \
stream_list.${MPASCore}.background \
stream_list.${MPASCore}.analysis \
stream_list.${MPASCore}.ensemble \
stream_list.${MPASCore}.control \
)
  ln -sfv $ModelConfigDir/rtpp/$staticfile .
end

rm ${StreamsFile}
cp -v $ModelConfigDir/rtpp/${StreamsFile} .
sed -i 's@{{nCells}}@'${nCellsEnsemble}'@' ${StreamsFile}
sed -i 's@{{TemplateFieldsPrefix}}@'${self_WorkDir}'/'${TemplateFieldsPrefix}'@' ${StreamsFile}
sed -i 's@{{InvariantFieldsPrefix}}@'${self_WorkDir}'/'${localInvariantFieldsPrefix}'@' ${StreamsFile}
sed -i 's@{{PRECISION}}@'${model__precision}'@' ${StreamsFile}

# determine analysis (center) output precision
ncdump -h ${centerFile} | grep uReconstruct | grep double
if ($status == 0) then
  set analysisPrecision=double
else
  ncdump -h ${centerFile} | grep uReconstruct | grep float
  if ($status == 0) then
    set analysisPrecision=single
  else
    echo "ERROR in $0 : cannot determine analysis precision" > ./FAIL
    exit 1
  endif
endif
sed -i 's@{{analysisPRECISION}}@'${analysisPrecision}'@' ${StreamsFile}

## copy/modify dynamic namelist
rm $NamelistFile
cp -v $ModelConfigDir/rtpp/${NamelistFile} .
sed -i 's@startTime@'${thisMPASNamelistDate}'@' $NamelistFile
sed -i 's@blockDecompPrefix@'${self_WorkDir}'/x'${meshRatioEnsemble}'.'${nCellsEnsemble}'@' ${NamelistFile}
sed -i 's@modelDT@'${TimeStepEnsemble}'@' $NamelistFile
sed -i 's@diffusionLengthScale@'${DiffusionLengthScaleEnsemble}'@' $NamelistFile

## modify namelist physics
sed -i 's@radtlwInterval@'${RadiationLWIntervalEnsemble}'@' $NamelistFile
sed -i 's@radtswInterval@'${RadiationSWIntervalEnsemble}'@' $NamelistFile
sed -i 's@physicsSuite@'${PhysicsSuiteEnsemble}'@' $NamelistFile
sed -i 's@micropScheme@'${MicrophysicsEnsemble}'@' $NamelistFile
sed -i 's@convectionScheme@'${ConvectionEnsemble}'@' $NamelistFile
sed -i 's@pblScheme@'${PBLEnsemble}'@' $NamelistFile
sed -i 's@gwdoScheme@'${GwdoEnsemble}'@' $NamelistFile
sed -i 's@radtCldScheme@'${RadiationCloudEnsemble}'@' $NamelistFile
sed -i 's@radtLWScheme@'${RadiationLWEnsemble}'@' $NamelistFile
sed -i 's@radtSWScheme@'${RadiationSWEnsemble}'@' $NamelistFile
sed -i 's@sfcLayerScheme@'${SfcLayerEnsemble}'@' $NamelistFile
sed -i 's@lsmScheme@'${LSMEnsemble}'@' $NamelistFile

## MPASJEDI variable configs
foreach file ($MPASJEDIVariablesFiles)
  ln -sfv $ModelConfigDir/$file .
end

# =============
# Generate yaml
# =============
## Copy jedi/applications yaml
set thisYAML = orig.yaml
cp -v ${ConfigDir}/jedi/applications/$appyaml $thisYAML

## streams
sed -i 's@{{EnsembleStreamsFile}}@'${self_WorkDir}'/'${StreamsFile}'@' $thisYAML

## namelist
sed -i 's@{{EnsembleNamelistFile}}@'${self_WorkDir}'/'${NamelistFile}'@' $thisYAML

## revise current date
sed -i 's@{{thisISO8601Date}}@'${thisISO8601Date}'@g' $thisYAML

# use the center (deterministic analysis) as the TemplateFieldsFileOuter
set meshFile = ${centerFile}
ln -sfv $meshFile ${TemplateFieldsFileOuter}

## file naming
sed -i 's@{{centerStateFile}}@'${centerFile}'@g' $thisYAML
sed -i 's@{{icStateDir}}@'${CyclingEnsFCICDir}'@g' $thisYAML
sed -i 's@{{icStatePrefix}}@'${anPrefix}'@g' $thisYAML
set prevYAML = $thisYAML

## variable configs
# RecenterVariables: perturbations preserved (met + GOCART aerosols) => the design choice
set RecenterVariables = ( \
  $StandardAnalysisVariables \
)
# StateVariables: superset that makes the recentered output a complete, startable
# model state.  Everything here but NOT in RecenterVariables is taken from the center.
set StateVariables = ( \
  $StandardAnalysisVariables \
  pressure_p \
  air_pressure \
  dry_air_density \
  air_potential_temperature \
  u \
  water_vapor_mixing_ratio_wrt_dry_air \
)
foreach hydro ($MPASHydroIncrementVariables)
  set StateVariables = ($StateVariables $hydro)
end
foreach VarGroup (Recenter State)
  if (${VarGroup} == Recenter) then
    set Variables = ($RecenterVariables)
  endif
  if (${VarGroup} == State) then
    set Variables = ($StateVariables)
  endif
  set VarSub = ""
  foreach var ($Variables)
    set VarSub = "$VarSub$var,"
  end
  # remove trailing comma
  set VarSub = `echo "$VarSub" | sed 's/.$//'`
  sed -i 's@{{'$VarGroup'Variables}}@'$VarSub'@' $prevYAML
end

## fill in the ensemble member list (single loop over nEnsFCMembers)
set indent = "`${nSpaces} 2`"
set enspsed = EnsembleMembers
cat >! ${enspsed}SEDF.yaml << EOF
/{{${enspsed}}}/c\
EOF

set ensFile = ${ensPrefix}.${thisMPASFileDate}.nc
set member = 1
while ( $member <= ${nEnsFCMembers} )
  set ensDir = $ensDirs[$member]
  set filename = ${ensDir}/${ensFile}
  if ( $member < ${nEnsFCMembers} ) then
    set filename = ${filename}\\
  endif
cat >>! ${enspsed}SEDF.yaml << EOF
${indent}- <<: *stateReadConfig\
${indent}  filename: ${filename}
EOF

  @ member++
end
set thisYAML = origMembers.yaml
sed -f ${enspsed}SEDF.yaml $prevYAML >! $thisYAML
rm ${enspsed}SEDF.yaml
mv $thisYAML $appyaml


# Run the executable
# ==================
ln -sfv ${myBuildDir}/${myEXE} ./
mpiexec ./${myEXE} $myYAML ./jedi.log >& jedi.log.all


# Check status
# ============
grep 'Run: Finishing oops.* with status = 0' jedi.log
if ( $status != 0 ) then
  echo "ERROR in $0 : jedi application failed" > ./FAIL
  exit 1
endif

# ================================================================================================

## change invariant fields to a link, keeping for transparency
rm ${localInvariantFieldsFile}
mv ${localInvariantFieldsFile}${OrigFileSuffix} ${localInvariantFieldsFile}

date

exit 0
