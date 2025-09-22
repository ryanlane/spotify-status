"""Spotify service wrapper.

Encapsulates interaction with spotipy client + lightweight caching so the
channel and push manager deal with a small, typed surface (`TrackInfo`).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Any, Callable

# NOTE: Avoid relative import ('.models') because the plugin may be loaded under a
# synthetic module name without a proper package parent, causing
# "attempted relative import with no known parent package".
# We attempt direct sibling path import first (normal case), then fall back to
# an explicit importlib load if necessary.
try:  # Standard absolute-style attempt first (loader may have inserted path)
    from models import TrackInfo  # type: ignore
except Exception:  # noqa: BLE001
    import importlib.util, sys
    from pathlib import Path
    _SERVICE_DIR = Path(__file__).parent
    _models_path = _SERVICE_DIR / "models.py"
    if _models_path.exists():
        unique_name = "spotify_status_models"
        # Reuse already loaded module if present
        existing = sys.modules.get(unique_name)
        if existing is not None:
            TrackInfo = getattr(existing, "TrackInfo", None)  # type: ignore
            if TrackInfo is None:
                raise ImportError("Loaded spotify_status_models missing TrackInfo")
        else:
            _spec = importlib.util.spec_from_file_location(unique_name, _models_path)
            if _spec and _spec.loader:  # type: ignore[attr-defined]
                _mod = importlib.util.module_from_spec(_spec)
                # Pre-register before execution so dataclass decorator can resolve module
                sys.modules[unique_name] = _mod
                try:
                    _spec.loader.exec_module(_mod)  # type: ignore[attr-defined]
                    TrackInfo = getattr(_mod, "TrackInfo")  # type: ignore
                except Exception as _import_err:  # noqa: BLE001
                    # Clean up failed registration to avoid poisoning subsequent attempts
                    sys.modules.pop(unique_name, None)
                    raise ImportError(f"Failed dynamic-load of models.py for service: {_import_err}") from _import_err
            else:
                raise ImportError("Could not construct spec for models.py in service dynamic import")
    else:
        raise ImportError("models.py not found adjacent to service.py; cannot proceed")

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self, spotipy_client, cache_ttl: int = 30):
        self._client = spotipy_client
        self._cache_ttl = cache_ttl
        # Cache holds TrackInfo instances; not type annotated here due to dynamic import constraints.
        self._cache = None  # TrackInfo or None
        self._cache_ts = None  # float timestamp or None

    def authorized(self) -> bool:
        return self._client is not None

    def get_current_track(self, *, market: Optional[str] = None, additional_types: Optional[str] = None):  # -> Optional[TrackInfo]
        """Return cached or fresh TrackInfo (annotation omitted for dynamic import safety)."""
        # Cache check
        if self._cache and self._cache_ts and (time.time() - self._cache_ts) < self._cache_ttl:
            return self._cache
        if not self._client:
            return None
        try:
            playback = self._client.current_playback(market=market, additional_types=additional_types)
            if not playback or not playback.get("item"):
                return None
            track = TrackInfo.from_playback(playback)
            self._cache = track
            self._cache_ts = time.time()
            return track
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to fetch current playback: %s", e)
            return None

    def invalidate_cache(self):
        self._cache = None
        self._cache_ts = None
