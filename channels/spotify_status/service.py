"""Spotify service wrapper.

Encapsulates interaction with spotipy client + lightweight caching so the
channel and push manager deal with a small, typed surface (`TrackInfo`).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Any, Callable

from .models import TrackInfo

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self, spotipy_client, cache_ttl: int = 30):
        self._client = spotipy_client
        self._cache_ttl = cache_ttl
        self._cache: Optional[TrackInfo] = None
        self._cache_ts: Optional[float] = None

    def authorized(self) -> bool:
        return self._client is not None

    def get_current_track(self, *, market: Optional[str] = None, additional_types: Optional[str] = None) -> Optional[TrackInfo]:
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
