# Optional footprint-aligned plume support

This standalone preprocessing tool fills **missing** plume-rise support where a
positive native emission pixel overlaps an MPAS cell and contains actual FINN
fire records. It writes a new package; no workflow default, pollutant flux,
existing input file, or supported cell is changed. It requires the optional
source-vegetation generation/staging interface from MPAS-Workflow PR #45 and a
model executable supporting `config_prm_source_vegetation` (gocartMPAS PR #45).

## Why and what it means

Conservative remapping spreads a native raster pixel's emissions across its
intersecting MPAS cells. Point assignment of FINN records can leave some of those
cells with emissions but zero fire-size support. This option aligns support with
the native emitting footprint; it does not assert that a FINN point lies inside
every receiving MPAS cell. It cannot recover missing detections.

The same FINN record may support multiple cells. **The resulting fire-area field
is not a conserved inventory of individual-fire area.** No emission mass is
created, remapped, or rescaled. This is an experimental allocation option, not a
validated universal correction to AOD or plume height.

For each eligible cell-day:

1. Require exactly zero existing mean fire area and positive staged target
   emissions that day. Optional `--cells` restricts the spatial scope.
2. Find positive native source pixels with positive-area overlap with the actual
   MPAS vertex polygon. Boundary-only contact is excluded.
3. Gather positive-area FINN records within those pixels using half-open bounds;
   use each accepted record once per receiving cell. Exact duplicate metadata
   records cause failure, because distinct fire identity cannot be inferred.
4. Set mean individual-fire area from those records and choose the area-dominant
   known PRM category using the existing FINN crosswalk (lowest category wins ties).
   Update category share, known-area fraction, and record count consistently.
   If there is no known positive-area category, leave the cell unchanged.

Unknown-category records can contribute to mean area and reduce the known-area
fraction; this matches the existing category aggregator. No records, nonpositive
native source, existing support, or zero target emissions leave support unchanged.
Other variables in the copied fire-size file remain unchanged: its older size
moments are **not** recomputed. This package targets the model path consuming
`firesize_biob_modis_avg`; do not use it as a replacement statistical FINN product.

## Input contract

Python dependencies: NumPy, SciPy, netCDF4, Shapely, pyproj; xarray is also needed
when verifying the workflow's existing mesh fingerprint.

The mesh must provide radians `latCell/lonCell`, `latVertex/lonVertex`, and MPAS
one-based `verticesOnCell` with `nEdgesOnCell`. Native inputs currently support
only increasing, regular, global, cell-centred latitude/longitude rasters with
one dated daily record, dimensions `(time,lat,lon)`, and CF time units. Pixel
bounds are inferred from the verified regular centres. Descending, regional,
curvilinear, multi-time, and non-cell-centred products are rejected; normalize
those explicitly before use. The source variable must represent the same
inventory used in the staged target emissions. Values are used as a positive
support mask, not as weights.

Manifest CSV columns:

```csv
date,finn,native,variable,target,target_variable
2024-09-13,/data/FINN_2024257.txt.gz,/data/qfed2.emis_oc.061.20240913.nc4,biomass,/data/QFED_oc_hourly.nc,oc_biob_modis
```

Paths must be absolute or relative to the invocation working directory. Dates
must be unique and ordered; a subset of dates is allowed. Native CF time and FINN
DAY are checked against the manifest. FINN year comes from the manifest because
DAY alone cannot establish a year. Target files require MPAS `xtime`, a
`(Time,nCells)` variable, and matching coordinates or the existing workflow
`mesh_fingerprint`. For legacy hashes, supply an optional `target_scrip` CSV
column pointing to the original remapping destination grid. Its fingerprint
must match the emissions metadata, and every cell centre and vertex is checked
against the supplied mesh (including ordering). Cell count alone is never accepted. Fire-size and vegetation timestamps must match exactly; the
vegetation mesh fingerprint must match. Input files are hashed in provenance.

## Geometry and numerical scope

MPAS edges are treated as spherical great-circle arcs. A cell-centred spherical
gnomonic projection makes these straight and handles longitude wrapping without
planar date-line polygons. Native raster latitude edges are densified at 0.02
and 0.01 degrees; both resolutions must agree about the positive-overlap
threshold (default 1 projected square metre), otherwise generation fails for
inspection. This is a geometric support decision, **not** an ESMF conservative
weight calculation or a physical overlap-area diagnostic. The projection uses
radius 6371220 m; the tiny area threshold is numerical, not a fire-size filter.
Degenerate polygons or geometry outside the projection hemisphere fail rather
than silently falling back to nearest-cell assignment. Near-threshold overlaps
remain a documented numerical limitation.

## Generate and explicitly opt in

```bash
python tools/prm_footprint_support.py \
  --mesh /data/invariant.nc \
  --manifest /data/support_manifest.csv \
  --fire-size /data/FINN_fire_size.nc \
  --vegetation /data/prm_source_vegetation.nc \
  --output-dir /scratch/experimental_support \
  --cells 15754,46475,118941
```

Omit `--cells` to examine all eligible cells. Indices are zero-based, as in Python.
The output directory must not exist. On failure the partial package is removed.
On success it contains `fire_size.nc`, `vegetation.nc`, and `provenance.json` with
input/output hashes, per-cell records, native pixel indices, categories, and
eligible/filled/unfilled counts. Always use the two output NetCDF files together.
Point a separate experiment's existing fire-size input at `fire_size.nc` and its
optional source-vegetation setting at `vegetation.nc`. The tool does not change
suite configuration or enable the model switch. Existing production runs remain
unchanged until explicitly configured otherwise.

## Validation and scientific evidence

Tests cover seam handling, boundary-only contact, half-open pixel membership,
duplicate rejection, failed-package cleanup, preserved supported/nonemitting
cells and inputs, consistent category diagnostics, and repeatability. Run:

```bash
python -m unittest discover -s test -p 'test_prm_footprint_support.py' -v
```

A prior matched 60-km September 13 12 UTC–September 14 12 UTC sensitivity changed
four cell-day entries in two California cells, with identical executable,
initial state, and QFED emissions. At F24, injection tops changed from zero to
748 and 602 m AGL. The dominant cell's surface PM2.5 fell from 416 to 152 µg/m³;
regional AOD changed from 0.13265 to 0.13114. AOD RMSE reductions were 0.37% for
OCI (705 samples), 0.55% for MODIS (169), and 3.06% for AERONET L1.5 (403).
These are one-day nearest-cell optical comparisons, not CRTM or a demonstration
of statistical significance or surface-PM validation. The experiment motivates
this optional tool but does not prove broader skill or a remedy for regional AOD
bias. Its original local planar footprint calculation is replaced here by the
spherical projection method above; a separate real-input generator check must
confirm the assignments before treating the products as equivalent.

The reusable generator was subsequently run on Casper for the same September
13–14 inputs and three audited cells. It reproduced all four mean-area/category
assignments exactly and left the unsupported September 13 cell unchanged. This
check used the original QFED SCRIP destination grid to validate the legacy mesh
fingerprint. The complete PRM test suite passed 30 tests (11 new tests plus 19
existing regressions). This validates input construction; it is not another
forecast or an end-to-end Cylc test.
