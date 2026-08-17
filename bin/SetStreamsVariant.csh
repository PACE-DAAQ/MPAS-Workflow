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
#   biobEmissions  : '' or finn|gfas|qfed       optional per-dimension override from the scenario YAML
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

# PRM fire-statistics file. PRMAreaFile is the legacy workflow variable name.
# Current PRM-author guidance makes only fire-size average mandatory; the same
# file may also contain optional AREA std and FRP avg/std. Resolve one filename
# here so Forecast and GOCART2G init_atmosphere use it consistently.
if ( $?PRMAreaFile ) then
  set prmAreaFileResolved = `echo "${PRMAreaFile}" | sed 's@{{nCells}}@'${nCells}'@' | sed 's@{{year}}@'${emissionYear}'@' | sed 's@{{grid}}@'${emissionGrid}'@'`
  sed -i 's@{{prmArea}}@'${prmAreaFileResolved}'@' ${StreamsFile}
endif

# shared FINN biomass-burning file (used by the 'finn' inventory and as the QFED iso/mnt fallback)
set FINN = "FINNv2.5.1_modvrs_nrt_MOZART_${emissionYear}_${emissionGrid}.static_hourly_netcdf3.nc"

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
# --------------------------------------------------------------------------------------------------
switch ($streamsVariant)
  case cntl:
    set vAnth = cams     ; set vBiog = cams ; set vBiob = finn ; breaksw
  case pert01:
    set vAnth = cams     ; set vBiog = cams ; set vBiob = gfas ; breaksw
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
# (3a) anthropogenic. BC/OC/SO2/CO/NH3 follow the selected anthropogenic inventory.
# ISO/MNT remain CAMS because the current CEDS ensemble definition does not provide
# compatible speciated VOC streams for those two fields.
switch ($vAnth)
  case cams:
    set anthBC  = "${emissionGrid}-${emissionYear}-anth_black-carbon.MPAS.nc"
    set anthOC  = "${emissionGrid}-${emissionYear}-anth_organic-carbon.MPAS.nc"
    set anthSO2 = "${emissionGrid}-${emissionYear}-anth_sulfur-dioxide.MPAS.nc"
    set anthCO  = "${emissionGrid}-${emissionYear}-anth_carbon-monoxide.MPAS.nc"
    set anthNH3 = "${emissionGrid}-${emissionYear}-anth_ammonia.MPAS.nc"
    breaksw
  case ceds:
    set anthBC  = "CEDS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.BC.nc"
    set anthOC  = "CEDS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.OC.nc"
    set anthSO2 = "CEDS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.SO2.nc"
    set anthCO  = "CEDS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.CO.nc"
    set anthNH3 = "CEDS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.NH3.nc"
    breaksw
  case cams-mix:
    set anthBC  = "${emissionGrid}-${emissionYear}-CAMS_MIX_anth_black-carbon.MPAS.nc"
    set anthOC  = "${emissionGrid}-${emissionYear}-CAMS_MIX_anth_organic-carbon.MPAS.nc"
    set anthSO2 = "${emissionGrid}-${emissionYear}-CAMS_MIX_anth_sulfur-dioxide.MPAS.nc"
    set anthCO  = "${emissionGrid}-${emissionYear}-CAMS_MIX_anth_carbon-monoxide.MPAS.nc"
    # CAMS regional-mix products in the current ensemble did not define a separate NH3
    # product, so keep NH3 from the CAMS global inventory unless/until a mixed NH3
    # product is explicitly added.
    set anthNH3 = "${emissionGrid}-${emissionYear}-anth_ammonia.MPAS.nc"
    breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown anth emissions '$vAnth'" > ./FAIL
    exit 1
endsw

# ISO/MNT remain CAMS for all anthropogenic variants.
set anthISO = "${emissionGrid}-${emissionYear}-anth_isoprene.MPAS.nc"
set anthMNT = "${emissionGrid}-${emissionYear}-anth_monoterpenes.MPAS.nc"

# (3b) biomass burning (7 species; QFED has no iso/mnt, falls back to FINN)
switch ($vBiob)
  case finn:
    set biobBC = "$FINN" ; set biobOC = "$FINN" ; set biobNH3 = "$FINN" ; set biobSO2 = "$FINN"
    set biobCO = "$FINN" ; set biobISO = "$FINN" ; set biobMNT = "$FINN"
    breaksw
  case gfas:
    set biobBC  = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.bc.hourly.nc"
    set biobOC  = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.oc.hourly.nc"
    set biobNH3 = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.nh3.hourly.nc"
    set biobSO2 = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.so2.hourly.nc"
    set biobCO  = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.co.hourly.nc"
    set biobISO = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.iso.hourly.nc"
    set biobMNT = "GFAS_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.mnt.hourly.nc"
    breaksw
  case qfed:
    set biobBC  = "QFED_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.bc.hourly.nc"
    set biobOC  = "QFED_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.oc.hourly.nc"
    set biobNH3 = "QFED_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.nh3.hourly.nc"
    set biobSO2 = "QFED_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.so2.hourly.nc"
    set biobCO  = "QFED_Glb_${emissionYear}_MPAS.${emissionGrid}.grid.co.hourly.nc"
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
    set biogCO   = "${emissionGrid}-${emissionYear}-biog_carbon-monoxide.MPAS.nc"
    set biogISO  = "${emissionGrid}-${emissionYear}-biog_isoprene.MPAS.nc"
    set biogMNT  = "${emissionGrid}-${emissionYear}-biog_other-monoterpenes.MPAS.nc"
    set biogAPIN = "${emissionGrid}-${emissionYear}-biog_alpha-pinene.MPAS.nc"
    set biogBPIN = "${emissionGrid}-${emissionYear}-biog_beta-pinene.MPAS.nc"
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
