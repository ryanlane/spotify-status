"""Push/event streaming management for Spotify channel.

Encapsulates polling thread, change detection, and listener dispatch so the
main channel object can focus on configuration and routing.
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class PushManager:
    def __init__(
        self,
        poll_interval: int,
        get_current_track: Callable[[], Optional[Dict[str, Any]]],
        webhook_url_getter: Callable[[], Optional[str]]
    ):
        self.poll_interval = max(3, int(poll_interval))
        self._get_current_track = get_current_track
        self._webhook_url_getter = webhook_url_getter
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._listeners_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_track_id: Optional[str] = None
        self._last_is_playing: Optional[bool] = None
        self._consecutive_errors = 0

    # Listener management -------------------------------------------------
    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._listeners_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)
        self.ensure_thread()

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._listeners_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def listener_count(self) -> int:
        with self._listeners_lock:
            return len(self._listeners)

    # Thread lifecycle ----------------------------------------------------
    def ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SpotifyPushLoop", daemon=True)
        self._thread.start()
        logger.info("[PushManager] Started push loop (interval=%ss)", self.poll_interval)

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            logger.info("[PushManager] Stop signal set")

    def thread_alive(self) -> bool:
        return self._thread.is_alive() if self._thread else False

    # Core loop -----------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._poll_and_emit()
                self._consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.warning("[PushManager] Poll error: %s", exc)
                else:
                    logger.error("[PushManager] Repeated poll errors (%d): %s", self._consecutive_errors, exc)
            backoff = min(self._consecutive_errors * 2, 30)
            time.sleep(self.poll_interval + backoff)

    # Poll & emit ---------------------------------------------------------
    def _poll_and_emit(self, force: bool = False) -> bool:
        track = self._get_current_track()
        if not track:
            return False
        track_id = track.get("track_id")
        is_playing = track.get("is_playing")
        changed_track = track_id and track_id != self._last_track_id
        changed_state = is_playing != self._last_is_playing
        if not force and not (changed_track or changed_state):
            return False
        event_type = "now_playing_changed" if changed_track else "playback_state_changed"
        event = {
            "channel_id": "com.spotify.status",
            "event_type": event_type,
            "payload": track,
            "ts": time.time(),
            "version": 1,
        }
        self._last_track_id = track_id
        self._last_is_playing = is_playing  # type: ignore[assignment]
        self._dispatch(event)
        return True

    def force_emit(self) -> bool:
        return self._poll_and_emit(force=True)

    # Dispatch ------------------------------------------------------------
    def _dispatch(self, event: Dict[str, Any]):
        with self._listeners_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PushManager] Listener raised: %s", exc)
        webhook_url = self._webhook_url_getter()
        if webhook_url:
            import requests  # local import to avoid always loading at module import
            try:
                requests.post(webhook_url, json=event, timeout=5)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[PushManager] Webhook POST failed: %s", exc)
