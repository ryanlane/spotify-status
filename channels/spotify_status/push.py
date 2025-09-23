"""Push/event streaming management for Spotify channel.

Encapsulates polling thread, change detection, and listener dispatch so the
main channel object can focus on configuration and routing.
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Dict, Any, List, Optional, Tuple

try:  # Optional dependency (may be present in host environment)
    import requests
    from requests import RequestException
except Exception:  # noqa: BLE001
    requests = None  # type: ignore
    class RequestException(Exception):  # fallback for typing
        pass

logger = logging.getLogger(__name__)


class PushManager:
    def __init__(
        self,
        poll_interval: int,
        get_current_track: Callable[[], Optional[Dict[str, Any]]],
        webhook_url_getter: Callable[[], Optional[str]],
        near_end_window_sec: float = 20.0,
        early_wake_offset_sec: float = 0.5,
        emit_playback_state_events: bool = False,
        playback_state_debounce_sec: float = 1.0,
    ):
        """Create push manager.

        poll_interval: base seconds between polls (floor enforced at 3 here; channel enforces 2 overall).
        near_end_window_sec: when remaining playback time <= this window we switch to
            an adaptive wake targeted at (remaining - early_wake_offset_sec).
        early_wake_offset_sec: seconds after the theoretical track end to wake; avoids
            querying *before* Spotify advances to the next item.
        """
        self.poll_interval = max(3, int(poll_interval))
        self._get_current_track = get_current_track
        self._webhook_url_getter = webhook_url_getter
        self._near_end_window_sec = max(5.0, float(near_end_window_sec))
        self._early_wake_offset_sec = max(0.1, float(early_wake_offset_sec))
        self._emit_playback_state_events = bool(emit_playback_state_events)
        self._playback_state_debounce_sec = max(0.1, float(playback_state_debounce_sec))
        self._last_state_event_ts: Optional[float] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._listeners_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_track_id: Optional[str] = None
        self._last_is_playing: Optional[bool] = None
        # Track last textual metadata so we only emit on meaningful song changes.
        self._last_artist_name: Optional[str] = None
        self._last_album_name: Optional[str] = None
        self._last_track_name: Optional[str] = None
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
        """Main polling loop with adaptive near-track-end logic.

        Goals:
        - Poll baseline every self.poll_interval seconds (>=2s) to detect new tracks.
        - If we are within ~20s of track end, schedule a wake close to (end - 0.5s)
          so the next track is picked up quickly (important for slow e-ink refresh cycle).
        - Apply simple error backoff without exploding the wake timing logic.
        """
        while not self._stop.is_set():
            start = time.time()
            remaining_to_end: Optional[float] = None
            try:
                _, remaining_to_end = self._poll_and_emit_with_remaining()
                self._consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001
                # We intentionally catch all here because network auth expiry, JSON shape
                # changes, or transient library errors should not kill the daemon thread.
                # Error count drives a simple backoff; detailed errors still logged.
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.warning("[PushManager] Poll error: %s", exc)
                else:
                    logger.error("[PushManager] Repeated poll errors (%d): %s", self._consecutive_errors, exc)
            # Base sleep including minimal error backoff
            backoff = min(self._consecutive_errors * 2, 30)
            sleep_for = self.poll_interval + backoff
            # Adaptive: if we have remaining time <=20s, attempt to wake near boundary
            if (
                remaining_to_end is not None
                and remaining_to_end <= self._near_end_window_sec
            ):
                # Wake shortly *after* expected boundary to let backend advance.
                target = max(
                    self._early_wake_offset_sec,
                    remaining_to_end - self._early_wake_offset_sec,
                )
                # But never sleep longer than base poll interval (so we still capture any manual skips)
                sleep_for = min(sleep_for, target)
            elapsed = time.time() - start
            # Avoid negative (can happen if heavy work) – ensure minimum 0.25s spacing
            sleep_for = max(0.25, sleep_for - elapsed)
            if self._stop.wait(timeout=sleep_for):  # Allows fast shutdown
                break

    # Poll & emit ---------------------------------------------------------
    def _poll_and_emit(self, force: bool = False) -> bool:
        track = self._get_current_track()
        if not track:
            # If we previously had a track id and now no playback, emit cleared event
            if self._last_track_id is not None:
                event = {
                    "channel_id": "com.spotify.status",
                    "event_type": "now_playing_cleared",
                    "payload": None,
                    "ts": time.time(),
                    "version": 1,
                }
                # Reset last-known metadata so next actual track emits normally
                self._last_track_id = None
                self._last_artist_name = None
                self._last_album_name = None
                self._last_track_name = None
                self._dispatch(event)
                return True
            return False
        track_id = track.get("track_id")
        is_playing = track.get("is_playing")
        # Normalize possible key variants produced upstream
        artist_name = track.get("artist") or track.get("artist_name")
        album_name = track.get("album") or track.get("album_name")
        track_name = track.get("name") or track.get("track_name")

        # Determine changes (metadata only) OR optionally playback state changes.
        # Metadata changes drive image refresh. Playback state (pause/resume)
        # optionally emits a lightweight event when enabled.
        changed_track_id = bool(track_id and track_id != self._last_track_id)
        changed_artist = artist_name != self._last_artist_name
        changed_album = album_name != self._last_album_name
        changed_title = track_name != self._last_track_name
        metadata_changed = changed_track_id or changed_artist or changed_album or changed_title

        # Optionally emit playback_state_changed when only is_playing toggles
        if (
            self._emit_playback_state_events
            and not metadata_changed
            and self._last_is_playing is not None
            and is_playing != self._last_is_playing
        ):
            now_ts = time.time()
            if (
                self._last_state_event_ts is None
                or (now_ts - self._last_state_event_ts) >= self._playback_state_debounce_sec
            ):
                # Debounce ensures rapid play/pause toggles (user tapping controls) do not
                # flood downstream consumers. We emit only after the configured quiet period.
                event = {
                    "channel_id": "com.spotify.status",
                    "event_type": "playback_state_changed",
                    "payload": {
                        "track_id": self._last_track_id,
                        "is_playing": is_playing,
                    },
                    "ts": now_ts,
                    "version": 1,
                }
                self._last_state_event_ts = now_ts
                self._dispatch(event)
                # We continue to evaluate metadata_changed after, no return here.

        # Only emit metadata event when metadata changed, unless forced
        if not force and not metadata_changed:
            # Update last play state for future state-change detection
            self._last_is_playing = is_playing  # type: ignore[assignment]
            return False

        event_type = "now_playing_changed"
        event = {
            "channel_id": "com.spotify.status",
            "event_type": event_type,
            "payload": track,
            "ts": time.time(),
            "version": 1,
        }
        self._last_track_id = track_id
        self._last_is_playing = is_playing  # type: ignore[assignment]
        self._last_artist_name = artist_name
        self._last_album_name = album_name
        self._last_track_name = track_name
        self._dispatch(event)
        return True

    def force_emit(self) -> bool:
        return self._poll_and_emit(force=True)

    def _poll_and_emit_with_remaining(self) -> Tuple[bool, Optional[float]]:
        """Wrapper returning (changed, remaining_seconds_to_track_end).

        Only calculates remaining time if a track is playing and duration/progress present.
        """
        track = self._get_current_track()
        if not track:
            return False, None
        duration = track.get("duration_ms") or 0
        progress = track.get("progress_ms") or 0
        remaining_ms = max(0, duration - progress)
        changed = self._poll_and_emit()
        return changed, (remaining_ms / 1000.0 if duration and remaining_ms else None)

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
        if webhook_url and requests is not None:
            try:
                requests.post(webhook_url, json=event, timeout=5)
            except RequestException as exc:  # network / HTTP layer issues
                logger.debug("[PushManager] Webhook POST failed: %s", exc)
        elif webhook_url and requests is None:
            logger.debug("[PushManager] Webhook URL configured but 'requests' not available")
