"""Mesh-native emissions preprocessing for MPAS-Workflow.

Original ESMF emissions-regridding methodology and utility lineage credited to
Duseong Jo (2021).  The workflow integration, mesh caching, sparse application,
and variable-resolution support are maintained here as MPAS-Workflow code.
"""
from .mesh import MpasMesh, NeighborTable
from .sparse_weights import SparseWeights

__all__ = ["MpasMesh", "NeighborTable", "SparseWeights"]
