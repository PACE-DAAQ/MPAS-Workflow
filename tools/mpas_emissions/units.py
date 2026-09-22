"""Small, explicit unit conversions used by regular-grid inventories."""
from __future__ import annotations
import re
import numpy as np

AVOGADRO = 6.02214076e23


def _norm(u: str) -> str:
    """Canonical spelling: lower case, ``^`` exponents without parentheses,
    so ``kg m-2 s-1``, ``kg m**-2 s**-1`` and ``kg m^(-2) s^(-1)`` all
    normalize to ``kg m^-2 s^-1``."""
    s = str(u).lower().replace("**", "^")
    s = re.sub(r"\^\(\s*([-+]?\d+)\s*\)", r"^\1", s)   # m^(-2) -> m^-2
    s = re.sub(r"(?<![\^\d])(-\d)", r"^\1", s)          # m-2 -> m^-2 (but not m^-2)
    return " ".join(s.split())


def convert(values, *, source_units: str, target_units: str, molecular_weight_g_mol: float | None = None, scale: float = 1.0):
    a = np.asarray(values, dtype=np.float64) * float(scale)
    su = _norm(source_units); tu = _norm(target_units)
    if su == tu or not source_units or not target_units:
        return a

    kg_s = {"kg m^-2 s^-1", "kg m^(-2) s^(-1)", "kg/m2/s", "kg m-2 s-1"}
    kg_day = {"kg m^-2 day^-1", "kg/m2/day", "kg m-2 day-1"}
    molcm = {"molecules cm^-2 s^-1", "molecules/cm2/s", "molecule cm^-2 s^-1"}

    if su in kg_day:
        a = a / 86400.0
        su = "kg m^-2 s^-1"
    if su in kg_s and tu in molcm:
        if molecular_weight_g_mol is None or molecular_weight_g_mol <= 0:
            raise ValueError("molecular_weight_g_mol is required for kg -> molecules conversion")
        # kg/m2/s * (1000 g/kg)/(MW g/mol) * N_A / (1e4 cm2/m2)
        return a * (0.1 * AVOGADRO / float(molecular_weight_g_mol))
    if su in kg_s and tu in kg_s:
        return a
    raise ValueError(f"unsupported unit conversion: {source_units!r} -> {target_units!r}")
