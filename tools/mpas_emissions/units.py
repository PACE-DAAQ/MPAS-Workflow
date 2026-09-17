"""Small, explicit unit conversions used by regular-grid inventories."""
from __future__ import annotations
import numpy as np

AVOGADRO = 6.02214076e23


import re

def _norm(u: str) -> str:
    text = str(u).lower().replace("**", "^")
    text = re.sub(r"(?<!\^)-([12])\b", r"^-\1", text)
    return " ".join(text.split())


def convert(values, *, source_units: str, target_units: str, molecular_weight_g_mol: float | None = None, scale: float = 1.0):
    a = np.asarray(values, dtype=np.float64) * float(scale)
    su = _norm(source_units); tu = _norm(target_units)
    if su == tu or not source_units or not target_units:
        return a

    # GBBEPx writes the factors transposed ("kg s-1 m-2"); same quantity.
    kg_s = {"kg m^-2 s^-1", "kg m^(-2) s^(-1)", "kg/m2/s", "kg m-2 s-1",
            "kg s^-1 m^-2", "kg/s/m2"}
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
