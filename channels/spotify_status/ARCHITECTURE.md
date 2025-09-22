# Spotify Status Channel – Internal Architecture

This plugin has been partially refactored to move towards a clean, testable structure.

## Modules
- `channel.py` – Orchestrator: initialization, settings persistence, FastAPI router construction, manifest & image request entrypoints.
- `renderer.py` – Pillow-based image rendering (status + fallback). Additional renderers (HTML/SVG) can implement a similar interface later.
- `models.py` – Lightweight dataclasses (currently `TrackInfo`). Keeps core logic decoupled from pydantic; host app can wrap if desired.
- `service.py` – `SpotifyService` wrapper around spotipy client providing caching + conversion to `TrackInfo`.
- `push.py` – `PushManager` encapsulates background polling, change detection and listener/webhook dispatch.

## Remaining Refactor Targets
1. Extract router building into a dedicated `routes.py` factory for slimmer `channel.py`.
2. Introduce pydantic models for API request/response validation (optional if host already validates).
3. Centralize configuration (env + JSON) using a `config.py` helper or pydantic `BaseSettings`.
4. Add unit tests:
   - Renderer image dimension & grayscale tests.
   - SpotifyService caching and error handling.
   - PushManager change emission logic with a fake clock or short interval.
5. Add HTML renderer abstraction and unify via a `BaseRenderer` protocol.
6. Provide graceful shutdown hook from host to stop `PushManager` (partial via `channel.stop`).

## Event Flow (Push)
```
PushManager thread -> poll current track -> detect change -> dispatch listeners -> optional webhook POST
```

## Image Generation Flow
```
request_image -> determine mode (pillow/html) -> renderer builds PIL Image -> encode -> return bytes (optional base64)
```

## Design Principles
- Single Responsibility per module.
- Dependency Inversion: channel depends on abstract behavior surfaces (renderer/service/push), enabling easier swapping & testing.
- Fail Softly: degraded mode when spotipy absent; image generation still works with placeholders.
- Explicit Caching: short TTL caching handled inside `SpotifyService` rather than scattered fields.

---
This document should evolve as further modularization is completed.
