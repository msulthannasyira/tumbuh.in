from __future__ import annotations

import functools
from typing import Dict, Optional

from api_documentation import (
    get_climate_data,
    get_landcover_and_vegetation,
    get_nighttime_lights,
    get_seasonal_pattern,
    get_soil_data,
    get_topography_data,
)


class GEEVariableService:
    """Wrapper around documented functions to provide tile-level data."""

    @staticmethod
    def collect(lat: float, lon: float, start_date: str, end_date: str) -> Dict:
        climate = safe_call(get_climate_data, lat=lat, lon=lon, start_date=start_date, end_date=end_date)
        soil = safe_call(get_soil_data, lat=lat, lon=lon)
        topo = safe_call(get_topography_data, lat=lat, lon=lon)
        landcover = safe_call(get_landcover_and_vegetation, lat=lat, lon=lon, date_str=end_date)
        seasonal = safe_call(get_seasonal_pattern, lat=lat, lon=lon)
        night = safe_call(get_nighttime_lights, lat=lat, lon=lon)

        return {
            "climate": climate,
            "soil": soil,
            "topography": topo,
            "landcover": landcover,
            "seasonal": seasonal,
            "nighttime": night,
        }


def safe_call(func, **kwargs) -> Dict:
    try:
        return func(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive logging
        return {"status": "error", "message": str(exc)}
