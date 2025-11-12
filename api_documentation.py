"""Google Earth Engine data access helpers used by the agro services.

This module wraps Earth Engine datasets to provide the climate, soil,
land-cover, and ancillary variables required by ``GEEVariableService``.
It assumes that the Earth Engine Python API (``earthengine-api``) is
installed and that a service-account JSON key is available locally.

Environment variables:
``GEE_SERVICE_FILE``    Absolute or relative path to the service account JSON.
``GEE_SERVICE_ACCOUNT`` Optional explicit service account e-mail. When not
                         provided, ``client_email`` from the JSON file is used.
``GOOGLE_APPLICATION_CREDENTIALS`` falls back to the same as ``GEE_SERVICE_FILE``
when present.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional

import ee
from ee.ee_exception import EEException
import logging
import time

_BASE_DIR = Path(__file__).resolve().parent
_EE_INITIALIZED = False

ESA_WORLDCOVER_2021 = "ESA/WorldCover/v200/2021"
ERA5_LAND_DAILY = "ECMWF/ERA5_LAND/DAILY_AGGR"
SOILGRIDS_PREFIX = "projects/soilgrids-isric/"
TERRACLIMATE_COLLECTION = "IDAHO_EPSCOR/TERRACLIMATE"
VIIRS_VCMCFG = "NOAA/VIIRS/DNB/MONTHLY_V1/VCMCFG"
SENTINEL2_SR = "COPERNICUS/S2_SR_HARMONIZED"
SRTM_ELEVATION = "USGS/SRTMGL1_003"

WORLDCOVER_LABELS = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / Sparse",
    70: "Snow / Ice",
    80: "Permanent water",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss & lichen",
}

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


class EarthEngineInitializationError(RuntimeError):
    """Raised when Earth Engine cannot be initialised."""


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


logger = logging.getLogger(__name__)


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (_BASE_DIR / candidate).resolve()
    return candidate


def _service_account_from_key(key_path: Path) -> str:
    with key_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    client_email = data.get("client_email")
    if not client_email:
        raise EarthEngineInitializationError(
            "client_email tidak ditemukan di file service account"
        )
    return client_email


def _ensure_initialized() -> None:
    global _EE_INITIALIZED
    if _EE_INITIALIZED:
        return
    try:
        ee.Initialize()
        _EE_INITIALIZED = True
        return
    except EEException:
        pass

    credential_path = (
        os.getenv("GEE_SERVICE_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or "serviceaccount.json"
    )
    key_path = _resolve_path(credential_path)
    if not key_path.exists():
        raise EarthEngineInitializationError(
            f"GEE credential file tidak ditemukan: {key_path}"
        )

    service_account = os.getenv("GEE_SERVICE_ACCOUNT") or _service_account_from_key(
        key_path
    )
    credentials = ee.ServiceAccountCredentials(service_account, str(key_path))
    ee.Initialize(credentials)
    _EE_INITIALIZED = True


def _geometry(lat: float, lon: float, buffer_m: int = 5000) -> ee.Geometry:
    point = ee.Geometry.Point([lon, lat])
    return point.buffer(buffer_m)


def _safe_float(value: Any, multiplier: float = 1.0, offset: float = 0.0) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(numeric):
        return numeric * multiplier + offset
    return None


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    filtered = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not filtered:
        return None
    return float(mean(filtered))


def get_climate_data(lat: float, lon: float, start_date: str, end_date: str) -> Dict[str, Any]:
    """Return ERA5-Land climate summary for the given location with graceful fallback."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=5000)
    logger.info("GEE[climate]: start ERA5-Land for lat=%s lon=%s start=%s end=%s", lat, lon, start_date, end_date)

    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date)
    lookback_days = 7
    max_lookback_days = 84
    attempts = 0
    collection = None
    size = 0

    while attempts * lookback_days <= max_lookback_days:
        query_start = ee.Date(start_dt.strftime("%Y-%m-%d"))
        query_end = ee.Date(end_dt.strftime("%Y-%m-%d")).advance(1, "day")

        collection = (
            ee.ImageCollection(ERA5_LAND_DAILY)
            .select(["temperature_2m", "total_precipitation_sum"])
            .filterDate(query_start, query_end)
            .sort("system:time_start")
        )

        size = collection.size().getInfo()
        logger.info("GEE[climate]: found %d images in query window (attempt %d)", size, attempts)
        if size > 0:
            break

        start_dt -= timedelta(days=lookback_days)
        end_dt -= timedelta(days=lookback_days)
        attempts += 1

    if not size or collection is None:
        raise ValueError(
            "ERA5-Land belum menyediakan data untuk rentang tanggal tersebut (hingga 12 minggu ke belakang)."
        )

    max_days = min(size, 31)
    image_list = collection.toList(max_days)
    records: List[Dict[str, Any]] = []

    for idx in range(max_days):
        image = ee.Image(image_list.get(idx))
        date_str = image.date().format("YYYY-MM-dd").getInfo()
        logger.debug("GEE[climate]: processing image %d date=%s", idx, date_str)
        reduced = image.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=1000,
            bestEffort=True,
            maxPixels=1_000_000_000,
        ).getInfo()

        temp_k = reduced.get("temperature_2m")
        precip_m = reduced.get("total_precipitation_sum")
        record = {
            "date": date_str,
            "temp_mean_c": _safe_float(temp_k, offset=-273.15),
            "precip_mm": _safe_float(precip_m, multiplier=1000.0),
        }
        records.append(record)

    avg_temp = _mean(record.get("temp_mean_c") for record in records)
    avg_precip = _mean(record.get("precip_mm") for record in records)

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": ERA5_LAND_DAILY,
        "data": records,
        "temp_mean_c": avg_temp,
        "precip_mm": avg_precip,
        "date_range": {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": end_dt.strftime("%Y-%m-%d"),
        },
    }


def get_soil_data(lat: float, lon: float) -> Dict[str, Any]:
    """Return SoilGrids properties for 0-5 cm depth."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=5000)
    logger.info("GEE[soil]: querying SoilGrids for lat=%s lon=%s", lat, lon)

    ph_image = ee.Image(SOILGRIDS_PREFIX + "phh2o_mean").select("phh2o_0-5cm_mean").rename("ph")
    sand_image = ee.Image(SOILGRIDS_PREFIX + "sand_mean").select("sand_0-5cm_mean").rename("sand")
    clay_image = ee.Image(SOILGRIDS_PREFIX + "clay_mean").select("clay_0-5cm_mean").rename("clay")
    soc_image = ee.Image(SOILGRIDS_PREFIX + "soc_mean").select("soc_0-5cm_mean").rename("soc")

    combined = ph_image.addBands([sand_image, clay_image, soc_image])
    reduced = combined.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=250,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()

    ph_value = _safe_float(reduced.get("ph"), multiplier=0.1)
    logger.info("GEE[soil]: got values ph=%s sand=%s clay=%s soc=%s", ph_value, reduced.get("sand"), reduced.get("clay"), reduced.get("soc"))
    sand_value = _safe_float(reduced.get("sand"))
    clay_value = _safe_float(reduced.get("clay"))
    soc_value = _safe_float(reduced.get("soc"))

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": "SoilGrids",
        "properties_at_0_5cm": {
            "ph": ph_value,
            "sand_g_kg": sand_value,
            "clay_g_kg": clay_value,
            "organic_carbon_g_kg": soc_value,
        },
    }


def get_topography_data(lat: float, lon: float) -> Dict[str, Any]:
    """Return SRTM elevation and slope."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=1000)
    logger.info("GEE[topo]: querying SRTM for lat=%s lon=%s", lat, lon)

    elevation = ee.Image(SRTM_ELEVATION)
    slope = ee.Terrain.slope(elevation)

    combined = elevation.rename("elevation").addBands(slope.rename("slope"))
    reduced = combined.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=90,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()
    logger.info("GEE[topo]: elevation=%s slope=%s", reduced.get("elevation"), reduced.get("slope"))

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": SRTM_ELEVATION,
        "data": {
            "elevation_meters": _safe_float(reduced.get("elevation")),
            "slope_degrees": _safe_float(reduced.get("slope")),
        },
    }


def _prepare_sentinel2(image: ee.Image) -> ee.Image:
    scaled = image.select(["B2", "B3", "B4", "B8", "B11"]).multiply(0.0001)
    return scaled.copyProperties(image, ["system:time_start"])


def _landcover_label(code: Optional[float]) -> Optional[str]:
    if code is None:
        return None
    return WORLDCOVER_LABELS.get(int(code))


def get_landcover_and_vegetation(lat: float, lon: float, date_str: str) -> Dict[str, Any]:
    """Return land cover class with NDVI/NDWI derived from Sentinel-2."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=2000)
    logger.info("GEE[landcover]: querying Sentinel-2 + WorldCover for lat=%s lon=%s date=%s", lat, lon, date_str)

    analysis_end = ee.Date(date_str)
    analysis_start = analysis_end.advance(-30, "day")

    s2_collection = (
        ee.ImageCollection(SENTINEL2_SR)
        .filterBounds(region)
        .filterDate(analysis_start, analysis_end.advance(1, "day"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50))
        .map(_prepare_sentinel2)
    )

    size = s2_collection.size().getInfo()
    logger.info("GEE[landcover]: Sentinel-2 candidate images: %d", size)
    if size == 0:
        raise ValueError("Tidak ada citra Sentinel-2 bersih di sekitar tanggal tersebut.")

    s2_image = s2_collection.median()
    ndvi = s2_image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndwi = s2_image.normalizedDifference(["B3", "B8"]).rename("NDWI")

    stats = s2_image.addBands([ndvi, ndwi]).reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()

    ndvi_value = _safe_float(stats.get("NDVI"))
    ndwi_value = _safe_float(stats.get("NDWI"))
    logger.info("GEE[landcover]: NDVI=%s NDWI=%s", ndvi_value, ndwi_value)

    worldcover = ee.Image(ESA_WORLDCOVER_2021)
    cover_stats = worldcover.reduceRegion(
        reducer=ee.Reducer.mode(),
        geometry=region,
        scale=100,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()

    landcover_code = cover_stats.get("Map")
    logger.info("GEE[landcover]: worldcover code=%s", landcover_code)

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": {
            "landcover": ESA_WORLDCOVER_2021,
            "vegetation": SENTINEL2_SR,
        },
        "date": date_str,
        "landcover_class": _landcover_label(_safe_float(landcover_code)),
        "vegetation_indices": {
            "ndvi": ndvi_value,
            "ndwi": ndwi_value,
        },
    }


def get_seasonal_pattern(lat: float, lon: float) -> Dict[str, Any]:
    """Return long-term precipitation metrics from TerraClimate."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=5000)
    logger.info("GEE[seasonal]: querying TerraClimate long-term precip for lat=%s lon=%s", lat, lon)

    today = ee.Date(datetime.utcnow().strftime("%Y-%m-%d"))
    start = today.advance(-10, "year")

    collection = (
        ee.ImageCollection(TERRACLIMATE_COLLECTION)
        .select("pr")
        .filterBounds(region)
        .filterDate(start, today)
    )

    if collection.size().getInfo() == 0:
        raise ValueError("Tidak ada data TerraClimate untuk lokasi ini.")

    mean_image = collection.mean()
    mean_stats = mean_image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=4000,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()
    logger.info("GEE[seasonal]: mean long-term precip=%s", mean_stats.get("pr"))

    long_term_avg = _safe_float(mean_stats.get("pr"))

    monthly_totals: List[Dict[str, Any]] = []
    for month in range(1, 13):
        monthly_collection = collection.filter(ee.Filter.calendarRange(month, month, "month"))
        msize = monthly_collection.size().getInfo()
        if msize == 0:
            continue
        logger.debug("GEE[seasonal]: month %d has %d images", month, msize)
        monthly_mean = monthly_collection.mean()
        monthly_stats = monthly_mean.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=4000,
            bestEffort=True,
            maxPixels=1_000_000_000,
        ).getInfo()
        monthly_totals.append(
            {
                "month": month,
                "precip_mm": _safe_float(monthly_stats.get("pr")),
            }
        )

    valid_months = [m for m in monthly_totals if m["precip_mm"] is not None]
    wettest = max(valid_months, key=lambda item: item["precip_mm"]) if valid_months else None
    driest = min(valid_months, key=lambda item: item["precip_mm"]) if valid_months else None
    if valid_months:
        logger.info("GEE[seasonal]: wettest=%s driest=%s", wettest, driest)

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": TERRACLIMATE_COLLECTION,
        "data": {
            "long_term_avg_precip_mm": long_term_avg,
            "wettest_month": MONTH_NAMES[(wettest["month"] - 1)] if wettest else None,
            "driest_month": MONTH_NAMES[(driest["month"] - 1)] if driest else None,
        },
        "long_term_avg_precip_mm": long_term_avg,
    }


def get_nighttime_lights(lat: float, lon: float) -> Dict[str, Any]:
    """Return the most recent VIIRS average radiance."""

    _ensure_initialized()
    region = _geometry(lat, lon, buffer_m=2000)
    logger.info("GEE[nighlights]: querying VIIRS for lat=%s lon=%s", lat, lon)

    end = ee.Date(datetime.utcnow().strftime("%Y-%m-%d"))
    start = end.advance(-3, "month")

    collection = (
        ee.ImageCollection(VIIRS_VCMCFG)
        .select("avg_rad")
        .filterBounds(region)
        .filterDate(start, end)
    )

    if collection.size().getInfo() == 0:
        raise ValueError("Tidak ada citra VIIRS dalam 3 bulan terakhir untuk lokasi ini.")

    image = ee.Image(collection.sort("system:time_start", False).first())
    stats = image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=500,
        bestEffort=True,
        maxPixels=1_000_000_000,
    ).getInfo()
    logger.info("GEE[nighlights]: radiance=%s", stats.get("avg_rad"))

    return {
        "status": "ok",
        "generated_at": _now(),
        "lat": lat,
        "lon": lon,
        "provider": VIIRS_VCMCFG,
        "radiance": _safe_float(stats.get("avg_rad")),
    }
