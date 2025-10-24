from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Dict, Iterable, List

from agro.models import Tile


def build_summary(area, tiles: Iterable[Tile]) -> Dict:
    tiles_list = list(tiles)
    tile_count = len(tiles_list)
    dominant_counter: Counter = Counter()
    env_summary: Dict[str, str] = {}

    ndvi_values = []
    ndwi_values = []
    precip_values = []

    for tile in tiles_list:
        recs = tile.gemini_recommendations or []
        if recs:
            dominant_counter[recs[0]["plant"]] += 1
        ndvi = tile.variables.get("landcover", {}).get("vegetation_indices", {}).get("ndvi")
        ndwi = tile.variables.get("landcover", {}).get("vegetation_indices", {}).get("ndwi")
        precip = tile.variables.get("seasonal", {}).get("data", {}).get("long_term_avg_precip_mm") or tile.variables.get("seasonal", {}).get("long_term_avg_precip_mm")
        if isinstance(ndvi, (int, float)):
            ndvi_values.append(ndvi)
        if isinstance(ndwi, (int, float)):
            ndwi_values.append(ndwi)
        if isinstance(precip, (int, float)):
            precip_values.append(precip)

    if ndvi_values:
        env_summary["Rata-rata NDVI"] = f"{mean(ndvi_values):.3f}"
    if ndwi_values:
        env_summary["Rata-rata NDWI"] = f"{mean(ndwi_values):.3f}"
    if precip_values:
        env_summary["Curah Hujan Bulanan"] = f"{mean(precip_values):.1f} mm"

    dominant = [
        {
            "plant": plant,
            "tiles": count,
            "avg_confidence": _average_confidence(tiles_list, plant),
        }
        for plant, count in dominant_counter.most_common()
    ]

    return {
        "tile_count": tile_count,
        "dominant_crops": dominant,
        "env_summary": env_summary,
        "processing_seconds": area.processing_seconds or 0,
    }


def _average_confidence(tiles: List[Tile], plant: str) -> float:
    vals = []
    for tile in tiles:
        for rec in tile.gemini_recommendations or []:
            if rec["plant"] == plant and isinstance(rec.get("confidence"), (int, float)):
                vals.append(rec["confidence"])
    return sum(vals) / len(vals) if vals else 0.0
