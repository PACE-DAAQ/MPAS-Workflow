"""Native MPAS mesh access and reusable spatial indexing.

The topology logic is adapted from the user's ``mpas_idx_neighbors.py`` idea:
read ``cellsOnCell``, ``nEdgesOnCell`` and (for regional meshes)
``bdyMaskCell`` directly from the MPAS mesh instead of reconstructing
neighbors geometrically.

For point-source emissions (e.g. FINN), cell centers are indexed on the unit
sphere with ``scipy.spatial.cKDTree``.  This avoids an O(N_points*N_cells)
nearest-cell search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Optional

import numpy as np
import xarray as xr
from scipy.spatial import cKDTree

DEFAULT_EARTH_RADIUS_M = 6_371_229.0


def _unit_xyz(lat_rad: np.ndarray, lon_rad: np.ndarray) -> np.ndarray:
    """Convert spherical coordinates to Cartesian unit vectors."""
    clat = np.cos(lat_rad)
    return np.column_stack(
        (clat * np.cos(lon_rad), clat * np.sin(lon_rad), np.sin(lat_rad))
    )


def _chord_to_angle(chord: np.ndarray) -> np.ndarray:
    """Convert unit-sphere chord length to great-circle angle [rad]."""
    return 2.0 * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))


@dataclass(frozen=True)
class NeighborTable:
    """0-based MPAS cell-neighbor table."""

    cell_mask: np.ndarray
    n_neighbors: np.ndarray
    neighbor_indices: np.ndarray


@dataclass
class MpasMesh:
    """A lightweight, cached view of an MPAS mesh/init file."""

    path: Path
    lat_cell: np.ndarray
    lon_cell: np.ndarray
    area_cell: np.ndarray
    n_edges_on_cell: np.ndarray
    cells_on_cell: np.ndarray
    boundary_mask: np.ndarray
    vertices_on_cell: Optional[np.ndarray] = None
    lat_vertex: Optional[np.ndarray] = None
    lon_vertex: Optional[np.ndarray] = None
    sphere_radius_m: float = DEFAULT_EARTH_RADIUS_M
    _tree_all: Optional[cKDTree] = field(default=None, init=False, repr=False)
    _tree_interior: Optional[cKDTree] = field(default=None, init=False, repr=False)
    _interior_ids: Optional[np.ndarray] = field(default=None, init=False, repr=False)

    @classmethod
    def open(
        cls,
        mesh_file: str | Path,
        *,
        boundary_var: str = "bdyMaskCell",
        neighbors_var: str = "cellsOnCell",
        nedges_var: str = "nEdgesOnCell",
    ) -> "MpasMesh":
        """Read the MPAS geometry/topology needed by emissions processing.

        ``bdyMaskCell`` is optional so the same class works for global meshes.
        When absent, every cell is treated as an interior cell.
        """
        path = Path(mesh_file)
        with xr.open_dataset(path, decode_times=False) as ds:
            required = ["latCell", "lonCell", "areaCell", neighbors_var, nedges_var]
            missing = [name for name in required if name not in ds]
            if missing:
                raise KeyError(f"MPAS mesh is missing required variables: {missing}")

            lat_cell = np.asarray(ds["latCell"].values, dtype=np.float64).squeeze()
            lon_cell = np.asarray(ds["lonCell"].values, dtype=np.float64).squeeze()
            area_cell = np.asarray(ds["areaCell"].values, dtype=np.float64).squeeze()
            cells_on_cell = np.asarray(ds[neighbors_var].values, dtype=np.int64).squeeze()
            n_edges_on_cell = np.asarray(ds[nedges_var].values, dtype=np.int64).squeeze()

            if boundary_var in ds:
                boundary_mask = np.asarray(ds[boundary_var].values, dtype=np.int64).squeeze()
            else:
                boundary_mask = np.zeros(lat_cell.shape, dtype=np.int64)

            vertices_on_cell = None
            lat_vertex = None
            lon_vertex = None
            if all(name in ds for name in ("verticesOnCell", "latVertex", "lonVertex")):
                vertices_on_cell = np.asarray(ds["verticesOnCell"].values, dtype=np.int64).squeeze()
                lat_vertex = np.asarray(ds["latVertex"].values, dtype=np.float64).squeeze()
                lon_vertex = np.asarray(ds["lonVertex"].values, dtype=np.float64).squeeze()

            sphere_radius_m = float(
                ds.attrs.get(
                    "sphere_radius",
                    ds.attrs.get("sphereRadius", DEFAULT_EARTH_RADIUS_M),
                )
            )

        if cells_on_cell.ndim != 2:
            raise ValueError("cellsOnCell must have shape (nCells, maxEdges)")
        if lat_cell.ndim != 1 or lon_cell.ndim != 1 or area_cell.ndim != 1:
            raise ValueError("latCell/lonCell/areaCell must be one-dimensional")
        if cells_on_cell.shape[0] != lat_cell.size:
            raise ValueError("cellsOnCell nCells does not match latCell")

        return cls(
            path=path,
            lat_cell=lat_cell,
            lon_cell=lon_cell,
            area_cell=area_cell,
            n_edges_on_cell=n_edges_on_cell,
            cells_on_cell=cells_on_cell,
            boundary_mask=boundary_mask,
            vertices_on_cell=vertices_on_cell,
            lat_vertex=lat_vertex,
            lon_vertex=lon_vertex,
            sphere_radius_m=sphere_radius_m,
        )

    @property
    def n_cells(self) -> int:
        return int(self.lat_cell.size)

    @property
    def max_edges(self) -> int:
        return int(self.cells_on_cell.shape[1])

    @property
    def interior_mask(self) -> np.ndarray:
        """True for cells MPAS marks as non-boundary."""
        return self.boundary_mask == 0

    @property
    def surface_fraction(self) -> float:
        """Fraction of the spherical Earth covered by MPAS cell areas."""
        sphere_area = 4.0 * np.pi * self.sphere_radius_m ** 2
        return float(np.sum(self.area_cell, dtype=np.float64) / sphere_area)

    @property
    def is_global(self) -> bool:
        """True when cell areas cover essentially the whole sphere."""
        return abs(self.surface_fraction - 1.0) <= 5.0e-4

    @property
    def lat_cell_deg(self) -> np.ndarray:
        return np.rad2deg(self.lat_cell)

    @property
    def lon_cell_deg(self) -> np.ndarray:
        return np.rad2deg(self.lon_cell)

    @property
    def local_hour_offset(self) -> np.ndarray:
        """Approximate local solar-time offset used by the legacy FINN workflow."""
        return np.rint(self.lon_cell_deg / 15.0).astype(np.int64) % 24

    @property
    def fingerprint(self) -> str:
        """Short content fingerprint suitable for cached SCRIP/weight filenames."""
        h = sha256()
        h.update(np.asarray([self.n_cells], dtype=np.int64).tobytes())
        for arr in (self.lat_cell, self.lon_cell, self.area_cell):
            h.update(np.ascontiguousarray(arr).view(np.uint8))
        return h.hexdigest()[:16]

    def neighbor_table(
        self,
        *,
        interior_only: bool = True,
        require_full_expected: bool = False,
    ) -> NeighborTable:
        """Build a consistent 0-based neighbor table.

        This follows ``mpas_idx_neighbors.py`` but uses a two-pass mask so a
        cell removed by ``require_full_expected`` is not left in another
        cell's saved neighbor list.
        """
        n_cells, max_edges = self.cells_on_cell.shape
        base_mask = self.interior_mask.copy() if interior_only else np.ones(n_cells, dtype=bool)
        keep_mask = base_mask.copy()

        if require_full_expected:
            for i in np.flatnonzero(base_mask):
                n_expected = int(self.n_edges_on_cell[i])
                raw = np.asarray(self.cells_on_cell[i, :n_expected], dtype=np.int64)
                valid = raw[(raw > 0) & (raw <= n_cells)] - 1
                valid = valid[base_mask[valid]]
                if valid.size != n_expected:
                    keep_mask[i] = False

        neighbor_indices = np.full((n_cells, max_edges), -1, dtype=np.int64)
        n_neighbors = np.zeros(n_cells, dtype=np.int64)

        for i in np.flatnonzero(keep_mask):
            n_expected = int(self.n_edges_on_cell[i])
            raw = np.asarray(self.cells_on_cell[i, :n_expected], dtype=np.int64)
            valid = raw[(raw > 0) & (raw <= n_cells)] - 1
            valid = valid[keep_mask[valid]]
            if valid.size == 0:
                keep_mask[i] = False
                continue
            n_neighbors[i] = valid.size
            neighbor_indices[i, : valid.size] = valid

        # Remove any references to cells dropped because they had zero neighbors.
        for i in np.flatnonzero(keep_mask):
            valid = neighbor_indices[i]
            valid = valid[(valid >= 0) & keep_mask[np.maximum(valid, 0)]]
            neighbor_indices[i, :] = -1
            neighbor_indices[i, : valid.size] = valid
            n_neighbors[i] = valid.size

        return NeighborTable(keep_mask, n_neighbors, neighbor_indices)

    def save_neighbor_cache(
        self,
        out_file: str | Path,
        *,
        interior_only: bool = True,
        require_full_expected: bool = False,
    ) -> Path:
        table = self.neighbor_table(
            interior_only=interior_only,
            require_full_expected=require_full_expected,
        )
        out = Path(out_file)
        np.savez_compressed(
            out,
            interior_mask=table.cell_mask,
            n_neigh=table.n_neighbors,
            neigh_indices=table.neighbor_indices,
            mesh_fingerprint=self.fingerprint,
        )
        return out

    def _tree(self, interior_only: bool) -> tuple[cKDTree, np.ndarray]:
        xyz = _unit_xyz(self.lat_cell, self.lon_cell)
        if interior_only:
            if self._tree_interior is None:
                ids = np.flatnonzero(self.interior_mask)
                if ids.size == 0:
                    raise ValueError("No interior cells are available in this mesh")
                self._interior_ids = ids
                self._tree_interior = cKDTree(xyz[ids])
            return self._tree_interior, self._interior_ids  # type: ignore[return-value]
        if self._tree_all is None:
            self._tree_all = cKDTree(xyz)
        return self._tree_all, np.arange(self.n_cells, dtype=np.int64)

    def points_inside_cells(
        self,
        cell_ids: np.ndarray,
        lat_deg: np.ndarray,
        lon_deg: np.ndarray,
        *,
        tolerance: float = 1.0e-12,
    ) -> np.ndarray:
        """Test points against their candidate convex spherical MPAS polygons.

        Each polygon edge defines a great-circle half-space.  Edge orientation
        is made independent of clockwise/counter-clockwise vertex ordering by
        using the MPAS cell center as the reference interior point.
        """
        ids = np.asarray(cell_ids, dtype=np.int64)
        lat = np.asarray(lat_deg, dtype=np.float64)
        lon = np.asarray(lon_deg, dtype=np.float64)
        if ids.shape != lat.shape or ids.shape != lon.shape:
            raise ValueError("cell_ids, lat_deg and lon_deg must have identical shapes")
        if self.vertices_on_cell is None or self.lat_vertex is None or self.lon_vertex is None:
            raise KeyError("MPAS vertex geometry is required for polygon containment")
        inside = np.zeros(ids.shape, dtype=bool)
        valid = (ids >= 0) & (ids < self.n_cells) & np.isfinite(lat) & np.isfinite(lon)
        if not np.any(valid):
            return inside
        pxyz = _unit_xyz(np.deg2rad(lat[valid]), np.deg2rad(lon[valid]))
        valid_positions = np.flatnonzero(valid)
        valid_ids = ids[valid]
        for cell in np.unique(valid_ids):
            pos_local = np.flatnonzero(valid_ids == cell)
            nedge = int(self.n_edges_on_cell[cell])
            raw = np.asarray(self.vertices_on_cell[cell, :nedge], dtype=np.int64)
            vids = raw[(raw > 0) & (raw <= self.lat_vertex.size)] - 1
            if vids.size < 3:
                continue
            vxyz = _unit_xyz(self.lat_vertex[vids], self.lon_vertex[vids])
            nxt = np.roll(vxyz, -1, axis=0)
            normals = np.cross(vxyz, nxt)
            center = _unit_xyz(
                np.asarray([self.lat_cell[cell]]), np.asarray([self.lon_cell[cell]])
            )[0]
            reference = normals @ center
            orient = np.where(reference >= 0.0, 1.0, -1.0)
            signed = (pxyz[pos_local] @ normals.T) * orient[None, :]
            ok = np.all(signed >= -tolerance, axis=1)
            inside[valid_positions[pos_local]] = ok
        return inside

    def nearest_cells(
        self,
        lat_deg: np.ndarray,
        lon_deg: np.ndarray,
        *,
        interior_only: bool = False,
        reject_outside: bool = False,
        max_distance_factor: float = 2.5,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Map geographic points to nearest MPAS cell centers.

        Returns
        -------
        cell_ids : ndarray[int]
            0-based MPAS cell indices. Invalid/rejected points are -1.
        distance_km : ndarray[float]
            Great-circle distance from each point to its nearest cell center.
        valid : ndarray[bool]
            True where an assignment is accepted.

        Notes
        -----
        For a regional mesh, ``reject_outside=True`` prevents remote fires from
        being dumped onto the nearest boundary cell.  The acceptance threshold
        is ``max_distance_factor`` times the target cell's equivalent-area
        radius. When MPAS vertex geometry is available, accepted candidates are
        additionally required to lie inside the spherical cell polygon.
        """
        lat = np.asarray(lat_deg, dtype=np.float64)
        lon = np.asarray(lon_deg, dtype=np.float64)
        if lat.shape != lon.shape:
            raise ValueError("lat_deg and lon_deg must have identical shapes")

        valid_geo = np.isfinite(lat) & np.isfinite(lon) & (lat >= -90.0) & (lat <= 90.0)
        cell_ids = np.full(lat.shape, -1, dtype=np.int64)
        distance_km = np.full(lat.shape, np.nan, dtype=np.float64)
        if not np.any(valid_geo):
            return cell_ids, distance_km, valid_geo

        query_xyz = _unit_xyz(np.deg2rad(lat[valid_geo]), np.deg2rad(lon[valid_geo]))
        tree, lookup_ids = self._tree(interior_only)
        chord, idx = tree.query(query_xyz, k=1)
        chosen = lookup_ids[np.asarray(idx, dtype=np.int64)]
        angular = _chord_to_angle(chord)
        dist_m = angular * self.sphere_radius_m

        accepted = np.ones(chosen.shape, dtype=bool)
        if reject_outside:
            if max_distance_factor <= 0:
                raise ValueError("max_distance_factor must be positive")
            equivalent_radius_m = np.sqrt(self.area_cell[chosen] / np.pi)
            accepted &= dist_m <= max_distance_factor * equivalent_radius_m
            # When native polygons are available, make the final regional-domain
            # decision geometrically exact for the candidate Voronoi cell.
            if self.vertices_on_cell is not None and self.lat_vertex is not None and self.lon_vertex is not None:
                poly_ok = self.points_inside_cells(
                    chosen, lat[valid_geo], lon[valid_geo]
                )
                accepted &= poly_ok

        tmp_ids = np.full(chosen.shape, -1, dtype=np.int64)
        tmp_ids[accepted] = chosen[accepted]
        cell_ids[valid_geo] = tmp_ids
        distance_km[valid_geo] = dist_m / 1000.0

        valid = valid_geo.copy()
        valid[valid_geo] = accepted
        return cell_ids, distance_km, valid
