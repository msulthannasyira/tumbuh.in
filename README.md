# Tumbuh.in

Tumbuh.in is a geospatial decision-support platform that combines Google Earth Engine (GEE) datasets with Google Gemini Grounding Search to recommend the five most promising crops for every configurable tile (default 15 x 15 metres) inside a farmer-selected area. Users can sketch polygons directly on an OpenStreetMap canvas, trigger data aggregation, and view AI-enriched insights through a responsive dashboard.

This document provides a complete reference for contributors, operators, and stakeholders who plan to deploy or extend the system.

---

## Table of Contents

1. [Solution Overview](#solution-overview)
2. [Feature Highlights](#feature-highlights)
3. [System Architecture](#system-architecture)
4. [Technology Stack](#technology-stack)
5. [Prerequisites](#prerequisites)
6. [Installation](#installation)
7. [Environment Configuration](#environment-configuration)
8. [Database Schema](#database-schema)
9. [Running the Application](#running-the-application)
10. [Dashboard Walkthrough](#dashboard-walkthrough)
11. [API Endpoints](#api-endpoints)
12. [Background Jobs & Data Flow](#background-jobs--data-flow)
13. [Testing Strategy](#testing-strategy)
14. [Troubleshooting](#troubleshooting)
15. [Deployment Notes](#deployment-notes)
16. [Security Considerations](#security-considerations)
17. [Performance Guidelines](#performance-guidelines)
18. [Roadmap Ideas](#roadmap-ideas)
19. [Contributing](#contributing)
20. [License](#license)

---

## Solution Overview

1. Users delineate an area of interest (AOI) on an interactive Leaflet map.
2. The backend tiles the polygon into 15 m grids, fetches multi-source geospatial variables via GEE, and persists them as `Tile` records.
3. Each tile is enriched by Gemini 2.5 Flash with Google Search grounding to produce ranked crop recommendations with confidence scores and rationales.
4. The dashboard visualises per-tile metrics, aggregated insights, and historical activity for authenticated users.

Use cases include preliminary crop planning, agronomic advisory services, and scenario modelling for local governments or cooperatives.

---

## Feature Highlights

- **Draw & Upload Polygons**: Freehand or precise polygon drawing using Leaflet.draw, with undo and clear support.
- **High-Resolution Tiling**: Converts AOIs into configurable grids (default 15 x 15 m) to capture micro-variations in soil, moisture, and climate.
- **GEE Integration**: Pulls NDVI/NDWI, precipitation, temperature, soil texture, elevation, and seasonal statistics through the helper methods defined in `api_documentation.py`.
- **AI Crop Advisory**: Invokes Gemini Grounding Search (defined in `gemini_service.py`) to recommend top five crops per tile, including optional rationales and confidence scores.
- **Interactive Dashboard**: Displays status badges, legends, modal drill-down, map overlays, and a paginated tile table. Activity feed includes shortcuts to detail views.
- **Authentication Layer**: Customised login/register/logout flow with `TumbuhLoginView` and protected endpoints for processing areas.
- **Event Telemetry (Optional)**: Hooks available in views to emit timing metadata for monitoring or analytics (see `area.processing_seconds`).

---

## System Architecture

```
┌──────────┐    Draw polygon     ┌────────────┐
│  Browser │ ─────────────────▶ │  Django    │
│  (Leaflet│    AJAX payload    │  Views     │
└──────────┘                    └────┬───────┘
																			│
																			▼
															 ┌────────────┐
															 │ Aggregator │
															 │  Services  │
															 └────┬───────┘
																		│ Tile geometries
																		▼
														 ┌──────────────┐
														 │ PolygonTiler │
														 └────┬─────────┘
																	│ Matrix + tile polys
																	▼
												┌─────────────────────┐
												│ GEE Variable Service│
												│ (Earth Engine APIs) │
												└────┬────────────────┘
														 │ Variables per tile
														 ▼
											┌─────────────────┐
											│ Gemini Service  │
											│ (Grounding AI)  │
											└────┬────────────┘
													 │ Crop recs
													 ▼
										┌──────────────────┐
										│ Django ORM       │
										│ (SQLite default) │
										└──────────────────┘
```

- **Frontend**: Served templates, static assets, and REST endpoints consumed via fetch.
- **Backend**: Django views orchestrate geo-processing with services housed in `agro/services/`.
- **Data Store**: Default SQLite for convenience; replaceable with Postgres for production.
- **External APIs**: Google Earth Engine (via service account) and Gemini 2.5 Flash (via API key).

---

## Technology Stack

| Layer          | Tools                                           |
| -------------- | ------------------------------------------------ |
| Backend        | Django 4, Django Rest utilities, Python 3.10+    |
| Frontend       | Django templates, Leaflet, Leaflet.draw, Vanilla JS |
| Styling        | Custom CSS (BEM-style naming), CSS variables     |
| Data           | SQLite (dev), Postgres/MySQL (recommended prod)  |
| Geospatial API | Google Earth Engine REST                        |
| AI Service     | Google Gemini 2.5 Flash with Google Search grounding |
| Auth           | Django auth (session-based)                      |

---

## Prerequisites

1. **Python 3.10 or higher** with `pip`.
2. **Google Earth Engine service account** and exported JSON key (`serviceaccount.json`).
3. **Google Gemini API key** with Grounding Search access.
4. Optional: Node.js 18+ if you plan to bundle additional frontend assets.
5. Optional: Docker for containerised deployment.

---

## Installation

```powershell
# Clone the repository
git clone https://github.com/urtir/TUMBUH.git
cd TUMBUH

# Create a virtual environment (recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

Ensure `serviceaccount.json` is placed in the project root and never committed (already ignored in `.gitignore`).

---

## Environment Configuration

Duplicate the example file and populate secrets:

```powershell
Copy-Item .env.example .env
```

Mandatory variables:

| Key                | Description                                               |
| ------------------ | --------------------------------------------------------- |
| `GOOGLE_API_KEY`   | Gemini key with Grounding Search enabled                  |
| `GEE_SERVICE_FILE` | Optional path override for the service account JSON       |
| `GEE_MAX_WORKERS`  | Optional max thread pool size for parallel GEE fetch (default 4) |
| `DJANGO_SECRET_KEY`| Override for production deployments                       |
| `DEBUG`            | Set to `False` in production                              |

You may expand `.env` with database configuration (e.g., `DATABASE_URL`) if you migrate away from SQLite.

---

## Database Schema

Key models (see `agro/models.py`):

- `Area`: Stores geometry (GeoJSON), tiling matrix, processing status, metadata, and summary statistics.
- `Tile`: Linked to an `Area`, stores centroid, raw variables, gemini recommendations (JSON), and indexing info.

Run migrations after installation:

```powershell
python manage.py migrate
```

For initial superuser setup:

```powershell
python manage.py createsuperuser
```

---

## Running the Application

```powershell
python manage.py runserver 0.0.0.0:8000
```

Visit `http://127.0.0.1:8000/` for the public landing page. Authenticated routes live at `/app/` and require login.

### Default URLs

| URL                     | Description                       |
| ----------------------- | --------------------------------- |
| `/`                     | Landing page + latest activity    |
| `/auth/login/`          | Login form                        |
| `/auth/register/`       | Self-service signup               |
| `/app/`                 | Dashboard with map + tools        |
| `/areas/process/`       | POST endpoint for polygon payload |
| `/areas/<uuid>/`        | Area summary JSON                 |
| `/areas/<uuid>/tiles/`  | Tile detail JSON                  |
| `/areas/<uuid>/insight/`| Authenticated area insight view   |

---

## Dashboard Walkthrough

1. **Landing Page**: Displays global stats, daily trends, and recent activity cards. Each activity offers a “Lihat hasil” shortcut (login required).
2. **Dashboard (`/app/`)**: Map workspace with polygon tools, legend, and tile detail modals. Variables and top crops display dynamically after processing.
3. **Area Insight**: Enhanced analytics view featuring dominant crop highlight, environment summary cards, Leaflet-based tile visualisation, paginated tile table, and modals for deeper recommendation context.

Map interactions rely on `static/js/app.js` while styling comes from `static/css/style.css`.

---

## API Endpoints

All APIs are session-protected unless noted.

| Method | Endpoint                    | Description                                  |
| ------ | --------------------------- | -------------------------------------------- |
| POST   | `/areas/process/`           | Accepts GeoJSON polygon + area name, triggers tiling and enrichment |
| GET    | `/areas/<uuid>/`            | Returns area metadata, aggregates, and status |
| GET    | `/areas/<uuid>/tiles/`      | Returns paginated tile list (JSON)            |
| GET    | `/areas/<uuid>/insight/`    | Renders HTML insight page                     |

The process endpoint expects payload:

```json
{
	"name": "Lahan Percobaan",
	"geometry": {
		"type": "Polygon",
		"coordinates": [ [ [106.8, -6.2], ... ] ]
	}
}
```

Error responses follow the schema `{ "status": "error", "message": "..." }` with appropriate HTTP status codes.

---

## Background Jobs & Data Flow

1. **Polygon Tiling** (`services/tiling.py`): Converts polygon to grid matrix, returns tile geometries.
2. **Variable Collection** (`services/gee_service.py`): For each tile centroid, fetches env metrics (climate, soil, landcover, seasonal, topography).
3. **Aggregation** (`services/aggregator.py`): Computes dominant crops, environment averages, and summary metrics.
4. **Gemini Enrichment** (`services/gemini_service.py`): Sends structured prompt to Gemini, maps response back to tiles.
5. **View Response**: `process_area` saves results, returns JSON containing area summary, aggregated metrics, and tile list.

Processing happens within a transaction to keep area/tile state consistent.

---

## Testing Strategy

- **Unit Tests** (`agro/tests.py`): Cover services, view workflows, and serializer logic with mock GEE/Gemini responses.
- **Integration Tests**: Validate tiling pipeline end-to-end using sample polygons.
- **Manual Script** (`scripts/test_gemini_grounding.py`): Quick verification that the Gemini key is valid and grounding is enabled.

Run all tests:

```powershell
python manage.py test
```

---

## Troubleshooting

| Symptom | Possible Cause | Fix |
| ------- | -------------- | --- |
| `INVALID_CREDENTIALS` from GEE | `serviceaccount.json` missing or misconfigured | Confirm JSON path and service account permissions |
| `PERMISSION_DENIED` from Gemini | API key lacks Grounding Search scope | Enable Grounding Search in Google AI Studio |
| Tiles fail to render | Polygon self-intersects or GeoJSON invalid | Simplify polygon and ensure coordinates are ordered correctly |
| Map shows no legend colors | Gemini response missing recommendations | Check Gemini quota and logs in `Area.metadata` |

Enable Django debug logging to inspect service calls by setting `LOG_LEVEL=DEBUG` and configuring handlers in `tumbuh_site/settings.py`.

---

## Deployment Notes

1. Use Postgres with PostGIS for production to handle larger datasets and spatial queries.
2. Configure Gunicorn or uvicorn/daphne behind Nginx for SSL termination.
3. Set `DEBUG=False`, `ALLOWED_HOSTS`, and rotate `DJANGO_SECRET_KEY`.
4. Store `serviceaccount.json` and `.env` secrets in a secure vault (e.g., Azure Key Vault, AWS Secrets Manager).
5. Schedule periodic cleanup jobs if tile volume grows (Celery or Django-crontab).

---

## Security Considerations

- Use HTTPS for all deployments; the default Gemini key should never be exposed client-side.
- Restrict the process endpoint to authenticated users; throttle requests if exposing publicly.
- Review CORS settings before embedding into other portals.
- Monitor `Area.metadata` for exception traces to avoid leaking sensitive stack traces in logs.

---

## Performance Guidelines

- Limit polygon size to avoid excessive tile counts (recommend max ~5,000 tiles).
- Enable caching for static assets via CDN.
- For heavy loads, offload Gemini enrichment to asynchronous workers and poll for completion.
- Tune `GEE_MAX_WORKERS` to balance GEE throughput and quota usage when fetching tile variables in parallel.
- Use database indexes on `Tile(area, row_index, col_index)` (already created via migrations).

---

## Roadmap Ideas

- Multi-polygon support for batch processing.
- Export results (CSV/GeoJSON/GeoPackage).
- Alerting workflow for anomalies (e.g., negative NDVI trends).
- Integration with IoT sensor data for near-real-time updates.
- Multi-language localisation beyond Bahasa Indonesia.

---

## Contributing

1. Fork the repository and create a feature branch (`git checkout -b feature/awesome-change`).
2. Run tests (`python manage.py test`) before opening a pull request.
3. Provide screenshots or GIFs for UI updates.
4. Document new environment variables or dependencies in this README.

Bug reports and feature requests are welcome via GitHub Issues.

---

## License

This project is released under the MIT License. See `LICENSE` for full text.
