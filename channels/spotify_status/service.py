"""Spotify service wrapper.

Encapsulates interaction with spotipy client + lightweight caching so the
channel and push manager deal with a small, typed surface (`TrackInfo`).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Any, Callable

# NOTE: Avoid relative import ('.models') because the plugin may be loaded under a
# synthetic module name without a proper package parent. We first attempt a
# direct import of the "models" package (now the canonical location). If that
# fails (e.g., name collision with an unrelated global module), we explicitly
# load our sibling package via path.
try:  # Normal case: plugin directory already on sys.path (set in channel.py)
    from models import TrackInfo  # type: ignore
except Exception:  # noqa: BLE001
    import importlib.util, sys
    from pathlib import Path
    _SERVICE_DIR = Path(__file__).parent
    _pkg_dir = _SERVICE_DIR / "models"
    _init_file = _pkg_dir / "__init__.py"
    if _init_file.exists():
        unique_name = "spotify_status_models"
        existing = sys.modules.get(unique_name)
        if existing is not None:
            TrackInfo = getattr(existing, "TrackInfo", None)  # type: ignore
            if TrackInfo is None:
                raise ImportError("Loaded spotify_status_models package missing TrackInfo")
        else:
            _spec = importlib.util.spec_from_file_location(
                unique_name,
                _init_file,
                submodule_search_locations=[str(_pkg_dir)],  # type: ignore[arg-type]
            )
            if _spec and _spec.loader:  # type: ignore[attr-defined]
                _mod = importlib.util.module_from_spec(_spec)
                sys.modules[unique_name] = _mod  # Pre-register for dataclass decorators
                try:
                    _spec.loader.exec_module(_mod)  # type: ignore[attr-defined]
                    TrackInfo = getattr(_mod, "TrackInfo")  # type: ignore
                except Exception as _import_err:  # noqa: BLE001
                    sys.modules.pop(unique_name, None)
                    raise ImportError(
                        f"Failed dynamic-load of models package for service: {_import_err}"
                    ) from _import_err
            else:
                raise ImportError("Could not construct spec for models package in service dynamic import")
    else:
        raise ImportError("models package not found adjacent to service.py; cannot proceed")

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
