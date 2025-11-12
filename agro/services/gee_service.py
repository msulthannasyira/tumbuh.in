from __future__ import annotations

import functools
from typing import Dict, Optional
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        logger = logging.getLogger(__name__)
        logger.info("Mulai collect variables untuk centroid (lat=%.6f, lon=%.6f)", lat, lon)

        providers = {
            "climate": (get_climate_data, {"lat": lat, "lon": lon, "start_date": start_date, "end_date": end_date}),
            "soil": (get_soil_data, {"lat": lat, "lon": lon}),
            "topography": (get_topography_data, {"lat": lat, "lon": lon}),
            "landcover": (get_landcover_and_vegetation, {"lat": lat, "lon": lon, "date_str": end_date}),
            "seasonal": (get_seasonal_pattern, {"lat": lat, "lon": lon}),
            "nighttime": (get_nighttime_lights, {"lat": lat, "lon": lon}),
        }

        # Parallelize provider calls to reduce wall-clock time per tile.
        max_workers = min(4, len(providers))
        results: Dict[str, Dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for name, (func, kwargs) in providers.items():
                future = executor.submit(safe_call, func, **kwargs)
                future_map[future] = name
                logger.debug("Submitted provider %s for centroid (lat=%.6f, lon=%.6f)", name, lat, lon)

            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    value = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("Provider %s failed for centroid lat=%.6f lon=%.6f: %s", name, lat, lon, exc)
                    value = {"status": "error", "message": str(exc)}
                results[name] = value

        return {
            "climate": results.get("climate"),
            "soil": results.get("soil"),
            "topography": results.get("topography"),
            "landcover": results.get("landcover"),
            "seasonal": results.get("seasonal"),
            "nighttime": results.get("nighttime"),
        }


def safe_call(func, **kwargs) -> Dict:
    logger = logging.getLogger(__name__)
    func_name = getattr(func, "__name__", str(func))
    # Human friendly label
    label = func_name
    lat = kwargs.get("lat")
    lon = kwargs.get("lon")
    try:
        logger.info("Memanggil GEE: %s for lat=%s lon=%s", label, lat, lon)
        start = time.perf_counter()
        result = func(**kwargs)
        duration = time.perf_counter() - start
        # If the function returns a dict with status, log accordingly
        status = None
        try:
            status = result.get("status") if isinstance(result, dict) else None
        except Exception:
            status = None
        if status == "ok" or status is None:
            logger.info("Selesai %s in %.2fs", label, duration)
        else:
            logger.warning("%s finished with status=%s in %.2fs", label, status, duration)
        return result
    except Exception as exc:  # pragma: no cover - defensive logging
        duration = time.perf_counter() - start
        logger.exception("Panggilan %s gagal setelah %.2fs: %s", label, duration, exc)
        return {"status": "error", "message": str(exc)}
