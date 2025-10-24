from __future__ import annotations

from typing import Dict

from agro.models import Area, Tile


def tile_to_dict(tile: Tile) -> Dict:
    return {
        "id": str(tile.id),
        "row_index": tile.row_index,
        "col_index": tile.col_index,
        "centroid": {"lat": tile.centroid_lat, "lon": tile.centroid_lon},
        "geometry": tile.geometry,
        "variables": tile.variables,
        "recommendations": tile.gemini_recommendations,
        "status": tile.status,
    }


def area_to_dict(area: Area) -> Dict:
    return {
        "id": str(area.id),
        "name": area.name,
        "status": area.status,
        "tile_size_m": area.tile_size_m,
        "matrix": area.matrix,
        "geometry": area.geometry,
        "processing_seconds": area.processing_seconds,
        "created_at": area.created_at.isoformat(),
    }
