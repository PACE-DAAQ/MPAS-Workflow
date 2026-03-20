#!/bin/csh -f

####################################
## workflow-relevant state variables
####################################
set MPASHydroIncrementVariables = ( \
  cloud_liquid_water \
  cloud_liquid_ice \
  graupel \
  rain_water \
  snow_water \
)
set MPASHydroStateVariables = (${MPASHydroIncrementVariables} cldfrac)

#set StandardAnalysisVariables = ( \
#  water_vapor_mixing_ratio_wrt_moist_air \
#  air_pressure_at_surface \
#  air_temperature \
#  northward_wind \
#  eastward_wind \
#)

set StandardAnalysisVariables = ( \
  water_vapor_mixing_ratio_wrt_moist_air \
  air_pressure_at_surface \
  air_temperature \
  northward_wind \
  eastward_wind \
  mass_fraction_of_hydrophobic_black_carbon_in_air \
  mass_fraction_of_hydrophilic_black_carbon_in_air \
  mass_fraction_of_hydrophobic_brown_carbon_in_air \
  mass_fraction_of_hydrophilic_brown_carbon_in_air \
  mass_fraction_of_hydrophobic_organic_carbon_in_air \
  mass_fraction_of_hydrophilic_organic_carbon_in_air \
  mass_fraction_of_dust001_in_air \
  mass_fraction_of_dust002_in_air \
  mass_fraction_of_dust003_in_air \
  mass_fraction_of_dust004_in_air \
  mass_fraction_of_dust005_in_air \
  mass_fraction_of_sea_salt001_in_air \
  mass_fraction_of_sea_salt002_in_air \
  mass_fraction_of_sea_salt003_in_air \
  mass_fraction_of_sea_salt004_in_air \
  mass_fraction_of_sea_salt005_in_air \
  mass_fraction_of_sulfate_in_air \
  mass_fraction_of_nitrate001_in_air \
  mass_fraction_of_nitrate002_in_air \
  mass_fraction_of_nitrate003_in_air \
)
set StandardStateVariables = ( \
  $StandardAnalysisVariables \
  air_potential_temperature \
  dry_air_density \
  u \
  water_vapor_mixing_ratio_wrt_dry_air \
  air_pressure \
  air_pressure_levels \
  landmask \
  seaice_fraction \
  snowc \
  skin_temperature_at_surface \
  ivgtyp \
  isltyp \
  snowh \
  vegetation_area_fraction \
  eastward_wind_at_10m \
  northward_wind_at_10m \
  lai \
  smois \
  tslb \
  pressure_p \
  relative_humidity \
)

set MPASJEDIVariablesFiles = (\
  geovars.yaml \
  obsop_name_map.yaml \
  keptvars.yaml \
)

set SACAStateVariables = ( \
  air_temperature \
  water_vapor_mixing_ratio_wrt_moist_air \
  air_potential_temperature \
  u \
  dry_air_density \
  water_vapor_mixing_ratio_wrt_dry_air \
  cloud_liquid_water \
  cloud_liquid_ice \
  snow_water \
  cloud_ice_number_concentration \
  cldfrac \
  xland \
  air_pressure \
  pressure_p \
  air_pressure_at_surface \
)
