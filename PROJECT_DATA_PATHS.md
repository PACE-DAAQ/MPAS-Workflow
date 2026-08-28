# nmmm0081 project data map (v1.12)

Verified from the supplied `list.Data(1).txt`, `list.EMIS_orig.txt`, MERRA
`download.sh`, and the MPAS-Workflow issue #2 discussion.

| Resource | Preferred path/source | Status / policy |
|---|---|---|
| Project root | `/glade/campaign/ncar/nmmm0081/Data` | verified |
| Seed/prebuilt emissions | `/glade/campaign/ncar/nmmm0081/Data/MPAS-Workflow/gocart2g/emission/all` | transitional fallback |
| CEDS 2024 source subsets | `/glade/campaign/ncar/nmmm0081/Data/EMIS/CEDS/orig/CEDS_Glb_0.1x0.1_2024_anthro_*_v2025_monthly.nc` | real local 0.1° files incl. NH3 |
| QFED 2024 source | `/glade/campaign/ncar/nmmm0081/Data/EMIS/QFED/orig/qfed2.emis_*.061.2024.nc4` | real local 3600×1800 daily files |
| GFAS native source | CAMS/ADS GFAS v1.2 | preferred; regenerate from native 0.1° grid |
| GFAS archived MPAS daily | `/glade/campaign/ncar/nmmm0081/Data/EMIS/GFAS/orig/GFAS_Glb_2024_MPAS.x1.163842.grid.daily.nc` | comparison/fallback only; already remapped |
| FINNv2.5 historical | `/gdex/data/d312009/...` | preferred GDEX source where available |
| FINNv2.5.1 2024 NRT | ACOM NRT or `${FINN_NRT_DIR}` cache | source-first daily files |
| FINNv1 fallback | ACOM legacy `FINNv1_<year>/GLOB_MOZ4_YYYYDDD.txt.gz` | lazy long-gap fallback |
| CAMS processed | `/glade/campaign/ncar/nmmm0081/Data/EMIS/CAMS` | some entries still symlink to old tutorial holdings |
| CAMS+regional MIXv2 raw | `/glade/campaign/ncar/nmmm0081/Data/EMIS/MIXv2/orig` | real regional monthly archive |
| AEIM-INDIA raw | `/glade/campaign/ncar/nmmm0081/Data/EMIS/AEIM-INDIA/orig` | real local files |
| Background LUTs | `/glade/campaign/ncar/nmmm0081/Data/BKG_DATA` | issue #2 preferred project location |
| Optics | `/glade/campaign/ncar/nmmm0081/Data/MPAS-Workflow/gocart2g/optics` | verified local copy |
| Legacy/prebuilt PRM | `/glade/campaign/ncar/nmmm0081/Data/MPAS-Workflow/gocart2g/prm` | comparison/fallback only |
| MERRA2-GMI aerosol chemistry | `/glade/campaign/ncar/nmmm0081/Data/MERRA2_GMI` | same-year 6-hour single-time files |
| MERRA2-GMI OVP chemistry | same directory, `monthly.2019MM.nc4` | controlled 2019 monthly OVP policy |
| GDAS | `/glade/campaign/ncar/nmmm0081/Data/GDAS` | symlinks to GDEX d083003 |

## Policy

The workflow prefers original/native data where a verified source is available,
but does not replace controlled chemistry provenance silently:

- FINN: GDEX/ACOM source-first, with FINNv1 only for configured long gaps.
- CEDS/QFED: use the archived native regular-grid source files directly.
- GFAS: download native CAMS/ADS GFAS v1.2 and independently remap to MPAS;
  the existing MPAS-grid GFAS file is retained only for comparison.
- Chemistry IC: use the archived MERRA2-GMI collection because OVP is not
  available after 2019; same-year aerosol files are combined with archived
  2019 monthly OVP.
- Background LUTs: use `Data/BKG_DATA` per issue #2.
- Optics: use the project-local optics archive.
