"""Optional, explicit scaling for emissions sensitivity experiments.

Scaling is deliberately confined to *emitted* species.  HNO3 is not an
emissions stream in the current MPAS-GOCART2G forecast configuration; HNO3
background/initial-condition scaling belongs in chemistry initialization, not
here.

Configuration example::

    scaling:
      default: 1.0
      species:
        nh3: 0.7
      fields:
        nh3_anth_sum: 0.8
      sectors:
        awb: 0.0

Factors are multiplicative.  ``species`` accepts any supplied alias (for
example ``NH3``, ``nh3`` or ``ammonia``); ``fields`` applies to the final MPAS
variable; and ``sectors`` is mainly useful for CAMS anthropogenic sectors.
"""
from __future__ import annotations

from typing import Iterable, Mapping


def _norm(x: object) -> str:
    return str(x).strip().lower().replace("_", "-")


def _lookup(mapping: Mapping | None, keys: Iterable[object]) -> float:
    if not mapping:
        return 1.0
    normalized = {_norm(k): float(v) for k, v in dict(mapping).items()}
    for k in keys:
        nk = _norm(k)
        if nk in normalized:
            return normalized[nk]
    return 1.0


def scaling_factor(
    cfg: Mapping | None,
    *,
    species_aliases: Iterable[object] = (),
    field: str | None = None,
    sector: str | None = None,
) -> float:
    """Return the multiplicative factor requested by a ``scaling`` block.

    ``cfg`` may be the whole inventory config or the scaling block itself.
    Missing scaling always means 1.0.
    """
    if not cfg:
        return 1.0
    if isinstance(cfg, Mapping) and "scaling" in cfg:
        block = cfg.get("scaling") or {}
    elif isinstance(cfg, Mapping) and set(cfg).issubset({"default", "species", "fields", "sectors"}):
        block = cfg
    else:
        return 1.0
    if not isinstance(block, Mapping):
        return float(block)
    factor = float(block.get("default", 1.0))
    factor *= _lookup(block.get("species"), species_aliases)
    if field is not None:
        factor *= _lookup(block.get("fields"), [field])
    if sector is not None:
        factor *= _lookup(block.get("sectors"), [sector])
    if factor < 0:
        raise ValueError(f"negative emissions scaling factor is not allowed: {factor}")
    return factor


def describe_scaling(cfg: Mapping | None) -> dict:
    """Return a JSON/YAML-friendly copy for output provenance."""
    if not cfg:
        return {"default": 1.0}
    if isinstance(cfg, Mapping) and "scaling" in cfg:
        block = cfg.get("scaling") or {}
    elif isinstance(cfg, Mapping) and set(cfg).issubset({"default", "species", "fields", "sectors"}):
        block = cfg
    else:
        return {"default": 1.0}
    if isinstance(block, Mapping):
        return dict(block)
    return {"default": float(block)}
