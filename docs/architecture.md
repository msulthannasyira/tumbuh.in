# Tumbuh Web App Architecture Draft

## Overview
The Django web application helps agronomists and farmers evaluate crop suitability within custom regions. Users sketch a polygon on an OpenStreetMap (OSM) canvas, triggering a pipeline that slices the region into configurable tiles (default 15×15 m), retrieves environmental variables for each tile from Google Earth Engine (GEE), enriches the dataset via Gemini Grounding Search, and stores the combined insights in SQLite for replay and analytics.

## Key Components

| Layer | Responsibility |
| --- | --- |
| Presentation (Django templates + Leaflet) | Interactive map (Leaflet + Leaflet.draw), tile hover/click tooltips, results table, 60:30:10 themed UI. |
| Application (Django views + async services) | Validate polygon input, orchestrate tiling, call data services, schedule async Gemini enrichment, expose REST endpoints for frontend data fetch. |
| Domain & Persistence (Django ORM) | `Area` and `Tile` models, storing raw variables and Gemini outputs with timestamps, UID per area. |
| Integration (Services) | `gee_service` for climate/soil/topography/land cover/seasonal/nighttime data using `api_documentation.py` logic. `gemini_service` for batch inference grounded to Google Search per `grounding_gemini_search_documentation.py`. |

## Data Flow
1. User draws polygon and submits (GeoJSON).
2. Backend validates geometry, converts to a list of tile centroids based on the requested grid size (default 15×15 m).
3. For each tile, services fetch:
   - Climate summary (ERA5)
   - Soil properties (SoilGrids)
   - Topography (SRTM)
   - Landcover + NDVI/NDWI (Sentinel-2 + ESA WorldCover)
   - Seasonal precipitation (TerraClimate)
   - Nighttime lights (VIIRS)
4. Tile data is persisted with status `COLLECTED`.
5. The system aggregates tile variables into a single Gemini prompt, requesting top 5 crop recommendations per tile with confidence.
6. Gemini response is parsed, stored on each tile, status set to `ENRICHED`.
7. Frontend fetches area+tile data via JSON endpoint, renders table + map layers.

## Async Strategy
- Django view triggers an `asyncio` task via `async_to_sync` pattern using `asgiref` to avoid blocking response. User receives area UID immediately and polling endpoint tracks progress.
- Background coroutine batches tile data and hits Gemini once, leveraging the synchronous `google.genai` client within a thread executor to maintain compatibility.

## Database Schema
- `Area`: `id (UUID)`, `name`, `geom (GeoJSON)`, `created_at`, `status`.
- `Tile`: `id (UUID)`, `area (FK)`, `row_index`, `col_index`, `centroid_lat`, `centroid_lon`, `variables (JSON)`, `gemini_recommendations (JSON)`, `status`, `created_at`, `updated_at`.

## APIs
- `POST /areas/`: Accepts polygon GeoJSON, tiling parameters, returns `{area_id}`.
- `GET /areas/<uuid>/`: Returns aggregated area metadata + tile preview.
- `GET /areas/<uuid>/tiles/`: Paginated tile data for table.
- `WS / SSE`: (Stretch) Real-time progress updates (optional backlog).

## Frontend Flow
- Map uses Leaflet with OSM tiles, custom theme matching 60:30:10 palette.
- Drawing tools for polygon; submission triggers loading overlay.
- Tiles displayed as overlay layers colored by top crop confidence; clicking reveals modal with top 5 crops and metrics.
- Bottom panel table with sortable columns for all variables + Gemini output.

## Security & Config
- Service account credentials loaded from `serviceaccount.json`. Provide environment variable fallback.
- Gemini API key loaded from env `GOOGLE_API_KEY`.
- Rate limits handled via request batching and minimal concurrency.

## Testing Strategy
- Unit tests for tiling algorithm, GEE service wrappers (mocked), Gemini prompt builder/parser.
- Integration tests for area submission workflow using Django test client.
- Frontend smoke tests via Django `LiveServerTestCase` (optional backlog).

## Next Steps
- Scaffold Django project structure.
- Implement reusable services using existing scripts.
- Develop UI components and asynchronous orchestration.
