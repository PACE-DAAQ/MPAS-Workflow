"""Retrieve native CAMS GFAS v1.2 data from the Copernicus ADS.

For historical cases such as 2024, the authoritative source is the ADS dataset
``cams-global-fire-emissions-gfas``.  Retrieval is deliberately chunked by
month so a failed request does not require re-downloading a very large year.
The ADS licence must be accepted once in the web UI and ~/.cdsapirc must point
to https://ads.atmosphere.copernicus.eu/api.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import calendar

GFAS_DATASET = "cams-global-fire-emissions-gfas"
GFAS_REQUIRED_VARIABLES = [
    "wildfire_flux_of_ammonia",
    "wildfire_flux_of_black_carbon",
    "wildfire_flux_of_carbon_monoxide",
    "wildfire_flux_of_isoprene",
    "wildfire_flux_of_organic_carbon",
    "wildfire_flux_of_sulphur_dioxide",
    "wildfire_flux_of_terpenes",
]


def _month_starts(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def fetch_gfas_ads(
    output_dir: str | Path,
    *,
    start: datetime,
    end: datetime,
    variables: list[str] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Download native 0.1-degree daily GFAS data as monthly NetCDF files."""
    try:
        import cdsapi
    except ImportError as exc:
        raise RuntimeError(
            "GFAS ADS retrieval requires cdsapi>=0.7.7. Install it in the emissions "
            "environment and configure ~/.cdsapirc for the Atmosphere Data Store."
        ) from exc

    variables = list(variables or GFAS_REQUIRED_VARIABLES)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client()
    outputs: list[Path] = []

    for year, month in _month_starts(start, end):
        first = max(start, datetime(year, month, 1))
        last_day = calendar.monthrange(year, month)[1]
        last = min(end, datetime(year, month, last_day, 23, 59, 59))
        if last < first:
            continue
        target = output_dir / f"GFAS_v1.2_native_0.1deg_{year}{month:02d}.nc"
        outputs.append(target)
        if target.exists() and not overwrite:
            continue
        request = {
            "variable": variables,
            "date": f"{first:%Y-%m-%d}/{last:%Y-%m-%d}",
            # GFAS v1.2 is archived in GRIB1; ADS supports server-side NetCDF
            # conversion. Keep this request key because it is the dataset's
            # historical/current GFAS API convention.
            "format": "netcdf",
        }
        tmp = target.with_suffix(target.suffix + ".part")
        if tmp.exists():
            tmp.unlink()
        client.retrieve(GFAS_DATASET, request, str(tmp))
        tmp.replace(target)
    return outputs
