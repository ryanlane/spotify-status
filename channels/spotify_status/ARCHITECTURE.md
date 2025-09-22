# Spotify Status Channel – Internal Architecture

The plugin has been refactored to a modular, test‑friendly structure separating orchestration, rendering, service integration, routing, and push/event logic.

## Module Overview
| Module | Responsibility |
| ------ | -------------- |
| `channel.py` | Orchestrator: lifecycle, settings persistence, manifest/status aggregation, image request entry point, push manager bootstrap. |
| `routes.py` | FastAPI router factory (`build_router(channel)`) – all HTTP endpoint definitions isolated from core logic. |
| `renderer.py` | Pillow renderer: builds status / fallback images (album art, text, progress bar). |
| `svg_renderer.py` | Optional SVG → PNG pipeline (Jinja2 templates + CairoSVG rasterization) with graceful fallback if deps missing. |
| `models.py` | Data model (`TrackInfo`) encapsulating normalized track/playback metadata. |
| `service.py` | `SpotifyService` wrapper around spotipy with TTL caching and conversion to `TrackInfo`. |
| `push.py` | `PushManager` polling thread: change detection, listener dispatch, optional webhook POST. |
| `ui/` | Static management UI assets (ESM modules, styles, index.html). |
| `svg/` | SVG Jinja2 templates (portrait / landscape / square). |

## Rendering Pipelines
Two rendering modes share a common contract (produce a Pillow `Image`):
1. Pillow (default) – `PillowRenderer` draws directly with Pillow APIs.
2. SVG (experimental) – `SvgRenderer` picks a template based on aspect ratio, renders via Jinja2, rasterizes with CairoSVG, then returns a Pillow image.

Fallback Strategy: If SVG rendering is requested but dependencies or template fail, it transparently falls back to the Pillow renderer.

## Push / Event Semantics
The push system polls Spotify at a configurable interval (minimum 3s). As of the latest change, events are emitted ONLY when track metadata changes (track id, title, artist, or album). Pure play/pause state transitions no longer produce a separate event, reducing noise. Event type: `now_playing_changed`.

Flow:
```
PushManager loop -> fetch current track (service + cache) -> compare metadata tuple -> if changed emit -> listeners + optional webhook
```

## Image Generation Flow
```
request_image -> parse options (size, grayscale, render_mode) ->
   if render_mode=svg & available -> SvgRenderer -> (else) PillowRenderer -> optional grayscale -> encode (JPEG|PNG) -> response dict (bytes + metadata)
```

## Settings & Auth
Settings are persisted to `data/settings.json`. OAuth token cache lives in `data/.spotify_cache` (spotipy standard). Redirect URI is auto-upgraded to `http://127.0.0.1:5000/...` if legacy values detected.

## Degraded Mode
If `spotipy` is missing the channel reports `degraded=True`; rendering still works (fallback images) but no Spotify lookups or push events occur until dependencies and auth are satisfied.

## Caching Strategy
`SpotifyService` maintains a simple in‑memory TTL cache (default 30s) to avoid excessive API calls. Push loop relies on this method, so repeated polling inside TTL does not re-hit the API.

## Error Handling Principles
* Fail softly for external/network errors (log + fallback image or no event).
* Avoid raising from polling thread—log with backoff.
* Never log secrets (client secret only indicated by presence boolean).

## Extensibility Points
* Additional renderers can be added implementing `render_image(track, width, height, ...) -> Image`.
* Future event types (e.g., playback_state_changed) can be reintroduced via strategy in `PushManager` if needed.
* Replace simple threading with asyncio or host-scheduled job if desired (keep surface identical for compatibility).

## Pending / Nice-to-Have Enhancements
1. Test suite (renderer dimension & grayscale tests; service cache expiry; push metadata change detection).
2. Pydantic request/response schemas if stricter validation needed.
3. Central configuration loader (env + file merge) to reduce inline parsing.
4. Base renderer protocol / registration mechanism.
5. Graceful shutdown integration hook (host invoking `channel.stop()` already partially covers this).

## Design Principles Recap
* Single Responsibility per module.
* Explicit, minimal surfaces between components.
* Graceful degradation when optional deps missing.
* Deterministic, low-noise event emission.
* Measurable caching scope (service-level only).

---
Last updated: Refactor introducing `routes.py`, SVG rendering, and metadata-only push events.
