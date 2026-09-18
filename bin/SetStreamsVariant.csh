#!/bin/csh -f

# (C) Copyright 2023 UCAR
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.

####################################################################################################
# Resolves the GOCART emission inventories for the forecast streams file and substitutes the
# per-species {{...}} placeholders left in ${StreamsFile} by config/mpas/forecast/streams.atmosphere.
#
# usage: source bin/SetStreamsVariant.csh   (run from the forecast WorkDir, after ${StreamsFile} is copied)
#
# Inputs (set externally, e.g. via config/auto/model.csh):
#   streamsVariant : cntl | pertNN     selects a default (anth/biog/biob) inventory combination
#   anthEmissions  : '' or cams|ceds|cams-mix   optional per-dimension override from the scenario YAML
#   biobEmissions  : '' or finn|gfas|qfed|gbbepx  optional per-dimension override from the scenario YAML
#   biogEmissions  : '' or cams                 optional per-dimension override from the scenario YAML
#   StreamsFile    : the local streams.atmosphere to modify
#
# To add/change a variant combination, edit the "variant -> inventory" switch in section (1).
# To add a new emission inventory, add a case to the corresponding switch in section (3).
####################################################################################################

# Mesh/year are known at Forecast run time; avoid hard-wiring x1.163842/2024.
if ( ! $?nCells ) then
  echo "ERROR SetStreamsVariant.csh: nCells is not defined" > ./FAIL
  exit 1
endif
if ( ! $?meshRatio ) then
  echo "ERROR SetStreamsVariant.csh: meshRatio is not defined" > ./FAIL
  exit 1
endif
set emissionGrid = "x${meshRatio}.${nCells}"
if ( ! $?thisCycleDate ) then
  echo "ERROR SetStreamsVariant.csh: thisCycleDate is not defined" > ./FAIL
  exit 1
endif
set emissionYear = `echo ${thisCycleDate} | cut -c 1-4`

# Unified emission filename rule (all inventories, all species):
#
#     <inventory>_<mesh>_<period>_<species>_<frequency>.nc
#
# Fields are separated by '_'; '.' appears only inside the mesh token and the
# extension, so a name can be split unambiguously. Species are lower case and
# short (bc, oc, so2, co, nh3, iso, mnt). Creation dates do NOT go in the name --
# the files carry time_created and the rest of their provenance as attributes,
# where it cannot drift from the filename.
#
# <period> is the 4-digit year for ANNUAL products (anthropogenic, biogenic) and
# a YYYYMMDD-YYYYMMDD window for products that only cover part of a year -- the
# fire inventories built for a campaign. Encoding it means the directory no
# longer has to: a file called ..._2024_... that actually holds 1 July to 30
# September is exactly the trap this rule removes.
set emissionPeriod = "${emissionYear}"
if ( $?EmissionPeriod ) then
  if ( "$EmissionPeriod" != "" ) set emissionPeriod = "${EmissionPeriod}"
endif

# PRM fire-statistics file. PRMAreaFile is the legacy workflow variable name.
# Current PRM-author guidance makes only fire-size average mandatory; the same
# file may also contain optional AREA std and FRP avg/std. Resolve one filename
# here so Forecast and GOCART2G init_atmosphere use it consistently.
if ( $?PRMAreaFile ) then
  set prmAreaFileResolved = `echo "${PRMAreaFile}" | sed 's@{{nCells}}@'${nCells}'@' | sed 's@{{year}}@'${emissionYear}'@' | sed 's@{{period}}@'${emissionPeriod}'@' | sed 's@{{grid}}@'${emissionGrid}'@'`
  sed -i 's@{{prmArea}}@'${prmAreaFileResolved}'@' ${StreamsFile}
endif

# shared FINN biomass-burning file (used by the 'finn' inventory and as the QFED iso/mnt fallback)
set FINN = "FINN_${emissionGrid}_${emissionPeriod}_biob_hourly.nc"

# an unset or empty variant behaves like the control combination
if ( ! $?streamsVariant ) set streamsVariant = cntl
if ( "$streamsVariant" == "" ) set streamsVariant = cntl

# --------------------------------------------------------------------------------------------------
# (0) optional per-ensemble-member override (model: member variants)
# --------------------------------------------------------------------------------------------------
# When the scenario sets 'model: member variants: [...]' (exported as the memberVariants array) and
# this is an ensemble member (ArgMember is set by Forecast.csh), member NN uses memberVariants[NN]
# instead of the scenario-wide 'streams variant'. Members beyond the list keep streamsVariant.
if ( $?memberVariants && $?ArgMember ) then
  if ( $#memberVariants > 0 && $ArgMember >= 1 && $ArgMember <= $#memberVariants ) then
    set streamsVariant = "$memberVariants[$ArgMember]"
    echo "SetStreamsVariant.csh (INFO): ensemble member $ArgMember uses streams variant '$streamsVariant'"
  endif
endif

# --------------------------------------------------------------------------------------------------
# (1) variant -> default (anth / biog / biob) inventory combination          [EDIT HERE to add perts]
#
# The nine combinations are a 3 (anth) x 3 (biob) sampling; 'cntl' is whichever
# corner the campaign treats as the control. It is cams/cams/GFAS because GFAS is
# this campaign's biomass inventory -- FINN supplies PRM fire size, not burned
# mass. cntl and pert01 were swapped for that reason; the SET of nine
# combinations is unchanged, so the emission ensemble PR #14 recenters on is
# unaffected.
#
# Change the control here rather than by defaulting 'biob emissions' in
# scenarios/defaults: that override is applied unconditionally in section (2) and
# would force every pert onto one inventory, collapsing the ensemble silently.
# --------------------------------------------------------------------------------------------------
switch ($streamsVariant)
  case cntl:
    set vAnth = cams     ; set vBiog = cams ; set vBiob = gfas ; breaksw
  case pert01:
    set vAnth = cams     ; set vBiog = cams ; set vBiob = finn ; breaksw
  case pert02:
    set vAnth = cams     ; set vBiog = cams ; set vBiob = qfed ; breaksw
  case pert03:
    set vAnth = ceds     ; set vBiog = cams ; set vBiob = finn ; breaksw
  case pert04:
    set vAnth = ceds     ; set vBiog = cams ; set vBiob = gfas ; breaksw
  case pert05:
    set vAnth = ceds     ; set vBiog = cams ; set vBiob = qfed ; breaksw
  case pert06:
    set vAnth = cams-mix ; set vBiog = cams ; set vBiob = finn ; breaksw
  case pert07:
    set vAnth = cams-mix ; set vBiog = cams ; set vBiob = gfas ; breaksw
  case pert08:
    set vAnth = cams-mix ; set vBiog = cams ; set vBiob = qfed ; breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown streams variant '$streamsVariant'" > ./FAIL
    exit 1
endsw

# --------------------------------------------------------------------------------------------------
# (2) optional per-dimension overrides from the scenario YAML (model: anth/biob/biog emissions)
# --------------------------------------------------------------------------------------------------
if ( $?anthEmissions ) then
  if ( "$anthEmissions" != "" ) set vAnth = "$anthEmissions"
endif
if ( $?biobEmissions ) then
  if ( "$biobEmissions" != "" ) set vBiob = "$biobEmissions"
endif
if ( $?biogEmissions ) then
  if ( "$biogEmissions" != "" ) set vBiog = "$biogEmissions"
endif

echo "SetStreamsVariant.csh (INFO): emission inventories: anth=$vAnth biog=$vBiog biob=$vBiob (variant=$streamsVariant)"

# --------------------------------------------------------------------------------------------------
# (3) inventory -> per-species filenames                                  [EDIT HERE to add inventories]
# --------------------------------------------------------------------------------------------------
# (3a) anthropogenic. BC/OC/SO2/CO follow the selected anthropogenic inventory.
# NH3/ISO/MNT stay CAMS for every variant, as they always have. CEDS does supply an NH3
# product, but tying NH3 to the anth selection changes which ammonia the whole ensemble
# burns, and nitrate is one of the species furthest from equilibrium in the current
# spin-up -- so that is a deliberate experiment, not a side effect of a filename change.
switch ($vAnth)
  case cams:
    set anthBC  = "CAMS-anth_${emissionGrid}_${emissionYear}_bc_monthly.nc"
    set anthOC  = "CAMS-anth_${emissionGrid}_${emissionYear}_oc_monthly.nc"
    set anthSO2 = "CAMS-anth_${emissionGrid}_${emissionYear}_so2_monthly.nc"
    set anthCO  = "CAMS-anth_${emissionGrid}_${emissionYear}_co_monthly.nc"
    breaksw
  case ceds:
    set anthBC  = "CEDS_${emissionGrid}_${emissionYear}_bc_monthly.nc"
    set anthOC  = "CEDS_${emissionGrid}_${emissionYear}_oc_monthly.nc"
    set anthSO2 = "CEDS_${emissionGrid}_${emissionYear}_so2_monthly.nc"
    set anthCO  = "CEDS_${emissionGrid}_${emissionYear}_co_monthly.nc"
    # config/emissions/ceds.example.yaml DOES build a CEDS ammonia product, under the
    # same unified rule, so switching NH3 onto the selected inventory later is this one
    # line -- nothing else has to change, and no file has to be renamed or restaged:
    #   set anthNH3 = "CEDS_${emissionGrid}_${emissionYear}_nh3_monthly.nc"
    # It is left inactive deliberately; see the note above the switch.
    breaksw
  case cams-mix:
    set anthBC  = "CAMS-MIX-anth_${emissionGrid}_${emissionYear}_bc_monthly.nc"
    set anthOC  = "CAMS-MIX-anth_${emissionGrid}_${emissionYear}_oc_monthly.nc"
    set anthSO2 = "CAMS-MIX-anth_${emissionGrid}_${emissionYear}_so2_monthly.nc"
    set anthCO  = "CAMS-MIX-anth_${emissionGrid}_${emissionYear}_co_monthly.nc"
    # product; NH3 comes from the CAMS global inventory for every variant anyway,
    # set once after this switch.
    breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown anth emissions '$vAnth'" > ./FAIL
    exit 1
endsw

# NH3/ISO/MNT remain CAMS for all anthropogenic variants.
set anthNH3 = "CAMS-anth_${emissionGrid}_${emissionYear}_nh3_monthly.nc"
set anthISO = "CAMS-anth_${emissionGrid}_${emissionYear}_iso_monthly.nc"
set anthMNT = "CAMS-anth_${emissionGrid}_${emissionYear}_mnt_monthly.nc"

# (3b) biomass burning (7 species; QFED and GBBEPx have no iso/mnt, fall back to FINN)
switch ($vBiob)
  case finn:
    set biobBC = "$FINN" ; set biobOC = "$FINN" ; set biobNH3 = "$FINN" ; set biobSO2 = "$FINN"
    set biobCO = "$FINN" ; set biobISO = "$FINN" ; set biobMNT = "$FINN"
    breaksw
  case gfas:
    set biobBC  = "GFAS_${emissionGrid}_${emissionPeriod}_bc_hourly.nc"
    set biobOC  = "GFAS_${emissionGrid}_${emissionPeriod}_oc_hourly.nc"
    set biobNH3 = "GFAS_${emissionGrid}_${emissionPeriod}_nh3_hourly.nc"
    set biobSO2 = "GFAS_${emissionGrid}_${emissionPeriod}_so2_hourly.nc"
    set biobCO  = "GFAS_${emissionGrid}_${emissionPeriod}_co_hourly.nc"
    set biobISO = "GFAS_${emissionGrid}_${emissionPeriod}_iso_hourly.nc"
    set biobMNT = "GFAS_${emissionGrid}_${emissionPeriod}_mnt_hourly.nc"
    breaksw
  case gbbepx:
    # NOAA blended VIIRS+MODIS. Like QFED it carries no iso/mnt, so those two fall
    # back to FINN. Note it is on a DIFFERENT source grid from GFAS/QFED
    # (1801 x 3600 node-centred vs 1800 x 3600 cell-centred), so it fingerprints
    # separately and must not reuse their regridding weights.
    set biobBC  = "GBBEPx_${emissionGrid}_${emissionPeriod}_bc_hourly.nc"
    set biobOC  = "GBBEPx_${emissionGrid}_${emissionPeriod}_oc_hourly.nc"
    set biobNH3 = "GBBEPx_${emissionGrid}_${emissionPeriod}_nh3_hourly.nc"
    set biobSO2 = "GBBEPx_${emissionGrid}_${emissionPeriod}_so2_hourly.nc"
    set biobCO  = "GBBEPx_${emissionGrid}_${emissionPeriod}_co_hourly.nc"
    set biobISO = "$FINN"
    set biobMNT = "$FINN"
    breaksw
  case qfed:
    set biobBC  = "QFED_${emissionGrid}_${emissionPeriod}_bc_hourly.nc"
    set biobOC  = "QFED_${emissionGrid}_${emissionPeriod}_oc_hourly.nc"
    set biobNH3 = "QFED_${emissionGrid}_${emissionPeriod}_nh3_hourly.nc"
    set biobSO2 = "QFED_${emissionGrid}_${emissionPeriod}_so2_hourly.nc"
    set biobCO  = "QFED_${emissionGrid}_${emissionPeriod}_co_hourly.nc"
    set biobISO = "$FINN"
    set biobMNT = "$FINN"
    breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown biob emissions '$vBiob'" > ./FAIL
    exit 1
endsw

# (3c) biogenic (5 species; single inventory for now)
switch ($vBiog)
  case cams:
    set biogCO   = "CAMS-biog_${emissionGrid}_${emissionYear}_co_monthly.nc"
    set biogISO  = "CAMS-biog_${emissionGrid}_${emissionYear}_iso_monthly.nc"
    set biogMNT  = "CAMS-biog_${emissionGrid}_${emissionYear}_mnt_monthly.nc"
    set biogAPIN = "CAMS-biog_${emissionGrid}_${emissionYear}_apin_monthly.nc"
    set biogBPIN = "CAMS-biog_${emissionGrid}_${emissionYear}_bpin_monthly.nc"
    breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown biog emissions '$vBiog'" > ./FAIL
    exit 1
endsw

# --------------------------------------------------------------------------------------------------
# (4) substitute placeholders in ${StreamsFile}
# --------------------------------------------------------------------------------------------------
sed -i 's@{{anthBC}}@'"$anthBC"'@'     ${StreamsFile}
sed -i 's@{{anthOC}}@'"$anthOC"'@'     ${StreamsFile}
sed -i 's@{{anthSO2}}@'"$anthSO2"'@'   ${StreamsFile}
sed -i 's@{{anthCO}}@'"$anthCO"'@'     ${StreamsFile}
sed -i 's@{{anthNH3}}@'"$anthNH3"'@'   ${StreamsFile}
sed -i 's@{{anthISO}}@'"$anthISO"'@'   ${StreamsFile}
sed -i 's@{{anthMNT}}@'"$anthMNT"'@'   ${StreamsFile}
sed -i 's@{{biobBC}}@'"$biobBC"'@'     ${StreamsFile}
sed -i 's@{{biobOC}}@'"$biobOC"'@'     ${StreamsFile}
sed -i 's@{{biobNH3}}@'"$biobNH3"'@'   ${StreamsFile}
sed -i 's@{{biobSO2}}@'"$biobSO2"'@'   ${StreamsFile}
sed -i 's@{{biobCO}}@'"$biobCO"'@'     ${StreamsFile}
sed -i 's@{{biobISO}}@'"$biobISO"'@'   ${StreamsFile}
sed -i 's@{{biobMNT}}@'"$biobMNT"'@'   ${StreamsFile}
sed -i 's@{{biogCO}}@'"$biogCO"'@'     ${StreamsFile}
sed -i 's@{{biogISO}}@'"$biogISO"'@'   ${StreamsFile}
sed -i 's@{{biogMNT}}@'"$biogMNT"'@'   ${StreamsFile}
sed -i 's@{{biogAPIN}}@'"$biogAPIN"'@' ${StreamsFile}
sed -i 's@{{biogBPIN}}@'"$biogBPIN"'@' ${StreamsFile}

# --------------------------------------------------------------------------------------------------
# (5) verify every referenced emission file actually exists
# --------------------------------------------------------------------------------------------------
# The names above and the names the emission tools WRITE are two independently
# maintained lists (bin/SetStreamsVariant.csh here, config/emissions/*.yaml and
# tools/mpas_emissions/cams_regrid.py there). Nothing couples them, so a rename on
# one side alone is silent until the model runs -- and MPAS then reports
# "CRITICAL ERROR: file '...' not in run directory", inside a batch job, after the
# IC has been read. Check here instead, where the message can name the variant and
# all the missing files at once.
## Only meaningful for a GOCART2G run. EmissionDir is set unconditionally by
## Build.py, so without this a plain meteorological forecast -- which never reads
## these streams -- would be failed for emission files it does not need.
## PhysicsSuite is resolved for the mesh in use by bin/Forecast.csh before this
## file is sourced; doBburnPrm would NOT work as the test, since it is set for
## every run regardless of whether chemistry is active.
set gocartOn = 0
if ( $?PhysicsSuite ) then
  if ( "$PhysicsSuite" == "MPAS-GOCART2G" ) set gocartOn = 1
endif
if ( ${gocartOn} == 1 && $?EmissionDir ) then
  set missingEmis = ()
  foreach f ( "$anthBC" "$anthOC" "$anthSO2" "$anthCO" "$anthNH3" "$anthISO" "$anthMNT" \
              "$biobBC" "$biobOC" "$biobNH3" "$biobSO2" "$biobCO" "$biobISO" "$biobMNT" \
              "$biogCO" "$biogISO" "$biogMNT" "$biogAPIN" "$biogBPIN" )
    if ( ! -e "${EmissionDir}/$f" ) set missingEmis = ( $missingEmis "$f" )
  end
  if ( $#missingEmis > 0 ) then
    echo "ERROR in SetStreamsVariant.csh : variant '$streamsVariant' (anth=$vAnth biog=$vBiog biob=$vBiob)" > ./FAIL
    echo "  references $#missingEmis emission file(s) absent from ${EmissionDir}:" >> ./FAIL
    foreach f ( $missingEmis )
      echo "    $f" >> ./FAIL
    end
    echo "  Filenames follow <inventory>_<mesh>_<period>_<species>_<frequency>.nc;" >> ./FAIL
    echo "  a mismatch usually means <period> disagrees (emission period = '$emissionPeriod')." >> ./FAIL
    cat ./FAIL
    exit 1
  endif
endif
