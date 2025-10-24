from __future__ import annotations

import logging
import json
import os

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from django.utils import timezone

from google import genai
from google.genai import types

from agro.services.gemini_service import GROUNDING_TOOL, _extract_text, _strip_markdown

logger = logging.getLogger(__name__)


@dataclass
class Bounds:
    north: float
    south: float
    east: float
    west: float
    center_lat: float
    center_lon: float


def _iter_coordinates(geometry: Dict[str, Any]) -> Iterable[tuple[float, float]]:
    if not geometry:
        return []

    geo_type = geometry.get("type")

    if geo_type == "Feature":
        return _iter_coordinates(geometry.get("geometry") or {})

    if geo_type == "FeatureCollection":
        features = geometry.get("features") or []
        for feature in features:
            inner = feature.get("geometry") if isinstance(feature, dict) else feature
            yield from _iter_coordinates(inner or {})
        return []

    coordinates = geometry.get("coordinates")

    if geo_type == "Polygon":
        for ring in coordinates or []:
            for lon, lat in ring:
                yield float(lon), float(lat)
        return []
    if geo_type == "MultiPolygon":
        for polygon in coordinates or []:
            for ring in polygon or []:
                for lon, lat in ring:
                    yield float(lon), float(lat)
        return []

    raise ValueError(f"Unsupported geometry type: {geo_type}")


def _bounds_from_coords(coords: Iterable[tuple[float, float]]) -> Optional[Bounds]:
    coord_list = list(coords)
    if not coord_list:
        return None

    lons, lats = zip(*coord_list)
    north = max(lats)
    south = min(lats)
    east = max(lons)
    west = min(lons)
    center_lat = (north + south) / 2
    center_lon = (east + west) / 2
    return Bounds(north=north, south=south, east=east, west=west, center_lat=center_lat, center_lon=center_lon)


def _bounds_from_matrix(matrix: Optional[List[List[Dict[str, Any]]]]) -> Optional[Bounds]:
    if not matrix:
        return None

    coords: List[tuple[float, float]] = []
    for row in matrix:
        for cell in row or []:
            if not isinstance(cell, dict):
                continue
            lat = cell.get("lat")
            lon = cell.get("lon")
            if lat is None or lon is None:
                continue
            coords.append((float(lon), float(lat)))

    return _bounds_from_coords(coords)


def _bounds_from_tiles(tiles: Optional[Sequence[Any]]) -> Optional[Bounds]:
    if not tiles:
        return None

    coords: List[tuple[float, float]] = []

    for tile in tiles:
        lat = None
        lon = None
        geometry = None

        if hasattr(tile, "centroid_lat") and hasattr(tile, "centroid_lon"):
            lat = getattr(tile, "centroid_lat")
            lon = getattr(tile, "centroid_lon")
        elif isinstance(tile, dict):
            lat = tile.get("centroid_lat")
            lon = tile.get("centroid_lon")
            if lat is None or lon is None:
                centroid = tile.get("centroid") or {}
                lat = centroid.get("lat")
                lon = centroid.get("lon")

        if lat is not None and lon is not None:
            coords.append((float(lon), float(lat)))

        if hasattr(tile, "geometry"):
            geometry = getattr(tile, "geometry")
        elif isinstance(tile, dict):
            geometry = tile.get("geometry")

        if isinstance(geometry, str):
            try:
                geometry = json.loads(geometry)
            except json.JSONDecodeError:
                geometry = None

        if isinstance(geometry, dict):
            try:
                coords.extend(_iter_coordinates(geometry))
            except ValueError:
                continue

    return _bounds_from_coords(coords)


def compute_bounds(
    geometry: Dict[str, Any],
    matrix: Optional[List[List[Dict[str, Any]]]] = None,
    tiles: Optional[Sequence[Any]] = None,
) -> Optional[Bounds]:
    bounds = None
    if geometry:
        try:
            bounds = _bounds_from_coords(_iter_coordinates(geometry))
        except ValueError:
            bounds = None

    if not bounds:
        bounds = _bounds_from_matrix(matrix)

    if not bounds:
        bounds = _bounds_from_tiles(tiles)

    return bounds


def _call_gemini(bounds: Bounds, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY belum diset untuk hyperlocal insight.")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        tools=[GROUNDING_TOOL],
        temperature=0.2,
    )

    crop_focus_clause = ""
    area_name = None
    primary_crops: List[str] = []
    if context:
        area_name = context.get("area_name")
        primary_crops = [crop for crop in context.get("primary_crops", []) if crop]

    if primary_crops:
        crop_list = ", ".join(primary_crops)
        crop_focus_clause = (
            f" Fokuskan seluruh insight pada dampak cuaca, risiko, dan dinamika pasar yang mempengaruhi komoditas {crop_list}. "
            "Setiap item wajib menyebutkan secara eksplisit bagaimana informasi tersebut berdampak pada daftar komoditas tersebut dan tolak insight mengenai komoditas lain."
        )

    instruction = (
        "Anda adalah analis agronomi digital. Gunakan Google Search grounding untuk mengambil informasi terbaru, faktual, dan terverifikasi dari sumber resmi. "
        "Kembalikan ringkasan hyperlokal minimal 3 dan maksimal 5 item dengan format JSON: {\"items\": [{\"title\": str, \"summary\": str, \"source_name\": str, \"source_url\": str}]}. "
        "Setiap item wajib fokus pada salah satu: prakiraan cuaca 3 hari ke depan, ringkasan curah hujan/iklim 7 hari terakhir, peringatan risiko (banjir, kekeringan, wabah tanaman, gempa), atau pembaruan harga komoditas pertanian. "
        "Cantumkan angka kunci (suhu, mm hujan, magnitudo, harga) serta tanggal/lokasi bila tersedia. Gunakan hanya informasi yang diterbitkan maksimal 30 hari terakhir; pengecualian harga komoditas dapat hingga 60 hari bila tidak ada yang terbaru. Kutip hanya sumber kredibel (BMKG, BNPB, Kementan, FAO, NASA, NOAA, USGS, kementerian/lembaga Indonesia, atau media nasional yang mengutip sumber resmi). Jangan menambahkan teks di luar JSON."
        f"{crop_focus_clause}"
    )

    location_context = (
        f"Pusat koordinat: lat {bounds.center_lat:.6f}, lon {bounds.center_lon:.6f}. "
        f"Batas area: utara {bounds.north:.6f}, selatan {bounds.south:.6f}, timur {bounds.east:.6f}, barat {bounds.west:.6f}."
    )

    parts = [
        types.Part.from_text(instruction),
        types.Part.from_text("Konteks lokasi:"),
        types.Part.from_text(location_context),
    ]

    if area_name:
        parts.append(types.Part.from_text(f"Nama area: {area_name}."))
    if primary_crops:
        parts.append(
            types.Part.from_text(
                "Daftar komoditas fokus: " + ", ".join(primary_crops) + ". Pastikan setiap insight relevan."
            )
        )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[types.Content(role="user", parts=parts)],
        config=config,
    )

    raw_text = _strip_markdown(_extract_text(response))
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini mengembalikan format tidak valid: {exc}. Raw: {raw_text}") from exc

    items = parsed.get("items")
    if not isinstance(items, list):
        raise ValueError("Respons Gemini tidak memiliki daftar 'items'.")

    sanitized: List[Dict[str, str]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "Insight")).strip() or "Insight"
        summary = str(entry.get("summary", "")).strip()
        source_name = str(entry.get("source_name", "Sumber kredibel")).strip()
        source_url = str(entry.get("source_url", "")).strip()
        if not source_url:
            continue
        sanitized.append(
            {
                "title": title,
                "summary": summary,
                "source_name": source_name,
                "source_url": source_url,
            }
        )

    if not sanitized:
        raise ValueError("Gemini tidak mengembalikan insight dengan sumber yang valid.")

    return {"items": sanitized}


def fetch_hyperlocal_insights(bounds: Bounds, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = timezone.now()
    try:
        payload = _call_gemini(bounds, context=context)
        items = payload.get("items", [])
    except Exception as exc:  # pragma: no cover - defensif
        logger.error("Gagal menghasilkan insight hyperlokal via Gemini: %s", exc)
        items = []

    return {
        "generated_at": now.isoformat(),
        "items": items,
    }


def build_hyperlocal_payload(
    geometry: Dict[str, Any],
    matrix: Optional[List[List[Dict[str, Any]]]] = None,
    tiles: Optional[Sequence[Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    bounds = compute_bounds(geometry, matrix, tiles)
    if not bounds:
        logger.warning("Tidak dapat menghitung batas area untuk hyperlocal insight.")
        return None
    payload = fetch_hyperlocal_insights(bounds, context=context)
    if payload is not None and context is not None:
        payload = dict(payload)
        payload["context"] = context
    return payload
