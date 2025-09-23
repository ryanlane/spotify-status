"""Lightweight dataclass model for currently playing track.

Separated from root models module to avoid dual model sources and make dynamic
imports simpler/safer when the plugin is loaded under a synthetic module name.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass(slots=True)
class TrackInfo:
    name: str
    artist: str
    album: str
    album_art_url: Optional[str]
    track_id: str
    progress_ms: int
    duration_ms: int
    is_playing: bool
    device: str

    @property
    def paused(self) -> bool:  # Derived convenience flag
        return not self.is_playing

    @property
    def progress_pct(self) -> float:
        return (self.progress_ms / self.duration_ms * 100) if self.duration_ms else 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["paused"] = self.paused
        data["progress_pct"] = self.progress_pct
        return data

    @classmethod
    def from_playback(cls, playback: Dict[str, Any]) -> "TrackInfo":
        item = playback.get("item") or {}
        return cls(
            name=item.get("name", "Unknown Track"),
            artist=", ".join([a.get("name", "?") for a in item.get("artists", [])]) or "Unknown Artist",
            album=item.get("album", {}).get("name", "Unknown Album"),
            album_art_url=(item.get("album", {}).get("images", []) or [{}])[0].get("url"),
            track_id=item.get("id", "unknown"),
            progress_ms=playback.get("progress_ms") or 0,
            duration_ms=item.get("duration_ms") or 0,
            is_playing=bool(playback.get("is_playing")),
            device=(playback.get("device") or {}).get("name", "Unknown"),
        )
