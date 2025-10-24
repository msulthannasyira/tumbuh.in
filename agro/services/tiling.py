from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from pyproj import Transformer
from shapely.geometry import Polygon, shape
from shapely.ops import transform, unary_union


@dataclass
class TileGeometry:
    row: int
    col: int
    polygon_wgs84: Polygon
    centroid: Tuple[float, float]

    def to_geojson(self) -> Dict:
        coordinates = [[float(x), float(y)] for x, y in self.polygon_wgs84.exterior.coords]
        return {
            "type": "Polygon",
            "coordinates": [coordinates],
        }


class PolygonTiler:
    def __init__(self, tile_size_m: float = 15.0) -> None:
        self.tile_size_m = tile_size_m
        self._to_meter = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
        self._to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform

    def tile(self, geojson: Dict) -> Tuple[List[List[Dict[str, float]]], List[TileGeometry]]:
        polygon_wgs84 = _to_polygon(geojson)
        if not polygon_wgs84.is_valid:
            polygon_wgs84 = polygon_wgs84.buffer(0)

        polygon_meter = transform(self._to_meter, polygon_wgs84)
        if polygon_meter.is_empty:
            raise ValueError("Polygon tidak valid untuk ditiling.")
        minx, miny, maxx, maxy = polygon_meter.bounds

        n_cols = max(1, math.ceil((maxx - minx) / self.tile_size_m))
        n_rows = max(1, math.ceil((maxy - miny) / self.tile_size_m))

        centroids_matrix: List[List[Dict[str, float]]] = []
        tiles: List[TileGeometry] = []

        for row in range(n_rows):
            row_centroids: List[Dict[str, float]] = []
            for col in range(n_cols):
                cell_minx = minx + col * self.tile_size_m
                cell_maxx = min(cell_minx + self.tile_size_m, maxx)
                cell_miny = miny + row * self.tile_size_m
                cell_maxy = min(cell_miny + self.tile_size_m, maxy)

                tile_poly_meter = Polygon([
                    (cell_minx, cell_miny),
                    (cell_maxx, cell_miny),
                    (cell_maxx, cell_maxy),
                    (cell_minx, cell_maxy),
                ])
                tile_poly_meter = tile_poly_meter.intersection(polygon_meter)
                if tile_poly_meter.is_empty:
                    row_centroids.append({"lat": None, "lon": None})
                    continue

                if tile_poly_meter.geom_type != "Polygon" and hasattr(tile_poly_meter, "geoms"):
                    candidates = [geom for geom in tile_poly_meter.geoms if geom.area > 0]
                    if not candidates:
                        row_centroids.append({"lat": None, "lon": None})
                        continue
                    tile_poly_meter = max(candidates, key=lambda g: g.area)

                tile_poly_wgs84 = transform(self._to_wgs84, tile_poly_meter)
                centroid_lon, centroid_lat = tile_poly_wgs84.centroid.x, tile_poly_wgs84.centroid.y

                row_centroids.append({"lat": centroid_lat, "lon": centroid_lon})
                tiles.append(
                    TileGeometry(
                        row=row,
                        col=col,
                        polygon_wgs84=tile_poly_wgs84,
                        centroid=(centroid_lat, centroid_lon),
                    )
                )
            centroids_matrix.append(row_centroids)
        return centroids_matrix, tiles


def flatten_tiles(matrix: Iterable[Iterable[Dict[str, float]]]) -> List[Dict[str, float]]:
    return [cell for row in matrix for cell in row if cell.get("lat") and cell.get("lon")]


def _to_polygon(geojson: Dict) -> Polygon:
    if not geojson:
        raise ValueError("GeoJSON polygon tidak boleh kosong.")

    gtype = geojson.get("type")

    if gtype == "Feature":
        geometry = geojson.get("geometry")
        if not geometry:
            raise ValueError("GeoJSON feature tidak memiliki geometry.")
        geom = shape(geometry)
    elif gtype == "FeatureCollection":
        features = geojson.get("features") or []
        geometries = [shape(f.get("geometry")) for f in features if f.get("geometry")]
        if not geometries:
            raise ValueError("FeatureCollection tidak memiliki geometry polygon.")
        geom = unary_union(geometries)
    else:
        geom = shape(geojson)

    if geom.is_empty:
        raise ValueError("Polygon yang diberikan kosong.")

    if geom.geom_type == "GeometryCollection":
        polygons = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polygons:
            raise ValueError("GeometryCollection tidak mengandung Polygon.")
        geom = unary_union(polygons)

    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)

    if geom.geom_type != "Polygon":
        raise ValueError("GeoJSON harus berupa Polygon.")

    return geom
