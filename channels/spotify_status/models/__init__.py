"""
Data models for Spotify Status Channel
"""

from datetime import datetime
from typing import Dict, Any, Optional, List
from pydantic import BaseModel


class SpotifyTrack(BaseModel):
    """Model for Spotify track information"""
    name: str
    artist: str
    album: str
    album_art_url: Optional[str] = None
    progress_ms: int = 0
    duration_ms: int = 0
    is_playing: bool = False
    device: str = "Unknown"


class ImageRequest(BaseModel):
    """Model for image generation requests"""
    width: int = 800
    height: int = 480
    include_metadata: bool = True
    include_progress: bool = True


class SpotifyConfig(BaseModel):
    """Model for Spotify configuration"""
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8080/callback"
    scopes: List[str] = ["user-read-currently-playing", "user-read-playback-state"]


class ChannelStatus(BaseModel):
    """Model for channel status"""
    active: bool
    healthy: bool
    last_update: datetime
    last_error: Optional[str] = None
    version: str = "1.0.0"
    spotify_connected: bool = False
    currently_playing: bool = False
    track_name: Optional[str] = None
