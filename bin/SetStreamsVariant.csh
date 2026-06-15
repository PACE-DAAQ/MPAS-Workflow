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

# shared FINN biomass-burning file (used by the 'finn' inventory and as the QFED iso/mnt fallback)
set FINN = "FINNv2.5.1_modvrs_nrt_MOZART_2024_x1.163842.static_hourly_netcdf3.nc"

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
# (3a) anthropogenic (only BC/OC/SO2/CO vary; NH3/ISO/MNT stay CAMS, hardcoded in the template)
switch ($vAnth)
  case cams:
    set anthBC  = "x1.163842-2024-anth_black-carbon.MPAS.nc"
    set anthOC  = "x1.163842-2024-anth_organic-carbon.MPAS.nc"
    set anthSO2 = "x1.163842-2024-anth_sulfur-dioxide.MPAS.nc"
    set anthCO  = "x1.163842-2024-anth_carbon-monoxide.MPAS.nc"
    breaksw
  case ceds:
    set anthBC  = "CEDS_Glb_2024_MPAS.x1.163842.grid.BC.nc"
    set anthOC  = "CEDS_Glb_2024_MPAS.x1.163842.grid.OC.nc"
    set anthSO2 = "CEDS_Glb_2024_MPAS.x1.163842.grid.SO2.nc"
    set anthCO  = "CEDS_Glb_2024_MPAS.x1.163842.grid.CO.nc"
    breaksw
  case cams-mix:
    set anthBC  = "x1.163842-2024-CAMS_MIX_anth_black-carbon.MPAS.nc"
    set anthOC  = "x1.163842-2024-CAMS_MIX_anth_organic-carbon.MPAS.nc"
    set anthSO2 = "x1.163842-2024-CAMS_MIX_anth_sulfur-dioxide.MPAS.nc"
    set anthCO  = "x1.163842-2024-CAMS_MIX_anth_carbon-monoxide.MPAS.nc"
    breaksw
  default:
    echo "ERROR in SetStreamsVariant.csh : unknown anth emissions '$vAnth'" > ./FAIL
    exit 1
endsw

# (3b) biomass burning (7 species; QFED has no iso/mnt, falls back to FINN)
switch ($vBiob)
  case finn:
    set biobBC = "$FINN" ; set biobOC = "$FINN" ; set biobNH3 = "$FINN" ; set biobSO2 = "$FINN"
    set biobCO = "$FINN" ; set biobISO = "$FINN" ; set biobMNT = "$FINN"
    breaksw
  case gfas:
    set biobBC  = "GFAS_Glb_2024_MPAS.x1.163842.grid.bc.hourly.nc"
    set biobOC  = "GFAS_Glb_2024_MPAS.x1.163842.grid.oc.hourly.nc"
    set biobNH3 = "GFAS_Glb_2024_MPAS.x1.163842.grid.nh3.hourly.nc"
    set biobSO2 = "GFAS_Glb_2024_MPAS.x1.163842.grid.so2.hourly.nc"
    set biobCO  = "GFAS_Glb_2024_MPAS.x1.163842.grid.co.hourly.nc"
    set biobISO = "GFAS_Glb_2024_MPAS.x1.163842.grid.iso.hourly.nc"
    set biobMNT = "GFAS_Glb_2024_MPAS.x1.163842.grid.mnt.hourly.nc"
    breaksw
  case qfed:
    set biobBC  = "QFED_Glb_2024_MPAS.x1.163842.grid.bc.hourly.nc"
    set biobOC  = "QFED_Glb_2024_MPAS.x1.163842.grid.oc.hourly.nc"
    set biobNH3 = "QFED_Glb_2024_MPAS.x1.163842.grid.nh3.hourly.nc"
    set biobSO2 = "QFED_Glb_2024_MPAS.x1.163842.grid.so2.hourly.nc"
    set biobCO  = "QFED_Glb_2024_MPAS.x1.163842.grid.co.hourly.nc"
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
    set biogCO   = "x1.163842-2024-biog_carbon-monoxide.MPAS.nc"
    set biogISO  = "x1.163842-2024-biog_isoprene.MPAS.nc"
    set biogMNT  = "x1.163842-2024-biog_other-monoterpenes.MPAS.nc"
    set biogAPIN = "x1.163842-2024-biog_alpha-pinene.MPAS.nc"
    set biogBPIN = "x1.163842-2024-biog_beta-pinene.MPAS.nc"
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
