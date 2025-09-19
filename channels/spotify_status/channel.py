"""
Spotify Status Channel Plugin for Mimir Platform

Displays currently playing Spotify track album art and metadata.
Integrates with Spotify Web API to fetch real-time listening status.
"""

import base64
import io
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

_SPOTIPY_AVAILABLE = False
spotipy = None  # type: ignore
SpotifyOAuth = None  # type: ignore

# We need logging configured before we can safely log missing dependency status
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:  # Optional dependency guard so router still mounts even if spotipy missing
    import spotipy  # type: ignore
    from spotipy.oauth2 import SpotifyOAuth  # type: ignore
    _SPOTIPY_AVAILABLE = True
except ImportError:  # noqa: BLE001
    logger.warning("[SpotifyStatusChannel] 'spotipy' not installed – channel running in degraded mode (no Spotify API calls)")
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# (logging already configured above)


class SpotifyStatusChannel:
    """
    Spotify Status Channel - Embedded Plugin for Mimir Platform
    
    Fetches currently playing track from Spotify and generates album art images
    with optional metadata overlay for e-paper display.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Spotify Status Channel with configuration"""
        self.config = config
        self.channel_dir = Path(__file__).parent
        self.data_dir = self.channel_dir / "data"
        self.ui_dir = self.channel_dir / "ui"
        self.settings_path = self.data_dir / "settings.json"
        self.token_path = self.data_dir / ".spotify_cache"  # spotipy cache path
        self.degraded = not _SPOTIPY_AVAILABLE
        
        # Ensure directories exist
        self.data_dir.mkdir(exist_ok=True)
        
        # Spotify API configuration
        self.spotify_client = None
        # Holds OAuth token metadata (expires_at, access_token, refresh_token, etc.) once authorized
        self.token_info = None  # type: Optional[Dict[str, Any]]
        self.last_track_cache = None
        self.cache_timestamp = None
        self.cache_duration = 30  # seconds

        # Load persisted settings if present and merge with provided config
        persisted = self._load_settings()
        if persisted:
            # Do not overwrite explicitly passed values; merge where missing
            base_spotify_cfg = self.config.get("spotify", {})
            merged = {**persisted.get("spotify", {}), **base_spotify_cfg}
            self.config["spotify"] = merged
        else:
            # Persist initial empty or provided settings skeleton
            self._save_settings(self.config)
        
        # Initialize Spotify client
        if not self.degraded:
            self._initialize_spotify_client()
        else:
            logger.info("[SpotifyStatusChannel] Initialization skipped (degraded mode: missing spotipy)")
        
        logger.info(f"Spotify Status Channel initialized: {self.channel_dir}")

    # ------------------------------------------------------------------
    # Settings Persistence
    # ------------------------------------------------------------------
    def _load_settings(self) -> Optional[Dict[str, Any]]:
        if not self.settings_path.exists():
            return None
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to load settings.json: {e}")
            return None

    def _save_settings(self, settings: Dict[str, Any]) -> bool:
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to save settings: {e}")
            return False
    
    def _initialize_spotify_client(self):
        """Initialize Spotify API client with OAuth"""
        if self.degraded:
            logger.debug("[SpotifyStatusChannel] Skipping client init due to degraded mode")
            return
        try:
            # Load Spotify credentials from config or environment
            spotify_config = self.config.get("spotify", {})
            client_id = spotify_config.get("client_id")
            client_secret = spotify_config.get("client_secret")
            redirect_uri = spotify_config.get("redirect_uri", "http://localhost:8080/callback")
            
            if not client_id or not client_secret:
                logger.error("Spotify client_id and client_secret required in config")
                return
            
            scope = "user-read-currently-playing user-read-playback-state"
            
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=scope,
                cache_path=str(self.data_dir / ".spotify_cache")
            )

            # Attempt to read cached token (so re-init after restart picks up prior auth)
            try:
                # Spotipy >=2.19 exposes cache handler; get access token without code to pull from cache
                token_info = auth_manager.get_cached_token() if hasattr(auth_manager, 'get_cached_token') else None
                if token_info and token_info.get('access_token'):
                    self.token_info = token_info
                    self.spotify_client = spotipy.Spotify(auth_manager=auth_manager)
                    logger.info("Spotify client initialized from cached token")
                else:
                    # Not yet authorized; keep auth_manager handy for later callback use
                    self.spotify_client = None
                    logger.info("Spotify client not yet authorized (no cached token)")
            except Exception as cache_e:  # noqa: BLE001
                logger.debug(f"[SpotifyStatusChannel] No cached token available: {cache_e}")
                self.spotify_client = None
            
        except Exception as e:
            logger.error(f"Failed to initialize Spotify client: {e}")
            self.spotify_client = None
    
    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get currently playing track from Spotify API"""
        if not self.spotify_client:
            return None
        
        # Check cache first
        if (self.last_track_cache and self.cache_timestamp and 
            (datetime.now() - self.cache_timestamp).total_seconds() < self.cache_duration):
            return self.last_track_cache
        
        try:
            spotify_cfg = self.config.get("spotify", {})
            market = spotify_cfg.get("market") or None
            additional_types = spotify_cfg.get("additional_types") or None
            # Validate additional_types (only track,episode allowed)
            if additional_types:
                cleaned = []
                for t in str(additional_types).split(','):
                    t = t.strip().lower()
                    if t in ("track", "episode") and t not in cleaned:
                        cleaned.append(t)
                additional_types = ",".join(cleaned) if cleaned else None
            current_track = self.spotify_client.current_playback(market=market, additional_types=additional_types)
            
            if not current_track or not current_track.get('is_playing'):
                return None
            
            track_info = {
                "name": current_track['item']['name'],
                "artist": ", ".join([artist['name'] for artist in current_track['item']['artists']]),
                "album": current_track['item']['album']['name'],
                "album_art_url": current_track['item']['album']['images'][0]['url'] if current_track['item']['album']['images'] else None,
                "progress_ms": current_track.get('progress_ms', 0),
                "duration_ms": current_track['item']['duration_ms'],
                "is_playing": current_track['is_playing'],
                "device": current_track['device']['name'] if current_track.get('device') else "Unknown"
            }
            
            # Cache the result
            self.last_track_cache = track_info
            self.cache_timestamp = datetime.now()
            
            return track_info
            
        except Exception as e:
            logger.error(f"Failed to get current track: {e}")
            return None
    
    def download_album_art(self, album_art_url: str) -> Optional[Image.Image]:
        """Download album art from Spotify"""
        try:
            response = requests.get(album_art_url, timeout=10)
            response.raise_for_status()
            
            image = Image.open(io.BytesIO(response.content))
            return image
            
        except Exception as e:
            logger.error(f"Failed to download album art: {e}")
            return None
    
    def create_status_image(self, track_info: Dict[str, Any], width: int = 800, height: int = 480) -> Image.Image:
        """Create status image with album art and metadata"""
        
        # Download album art
        album_art = None
        if track_info.get('album_art_url'):
            album_art = self.download_album_art(track_info['album_art_url'])
        
        # Create base image
        image = Image.new('RGB', (width, height), color='white')
        draw = ImageDraw.Draw(image)
        
        if album_art:
            # Resize album art to fit display
            art_size = min(width, height) - 100  # Leave space for text
            album_art = album_art.resize((art_size, art_size), Image.Resampling.LANCZOS)
            
            # Center album art
            art_x = (width - art_size) // 2
            art_y = 20
            image.paste(album_art, (art_x, art_y))
            
            # Add metadata below album art
            text_y = art_y + art_size + 20
        else:
            # No album art, show large text
            text_y = 50
            draw.rectangle([width//4, 50, 3*width//4, height//2], fill='lightgray', outline='black')
            draw.text((width//2, height//4), "♪", font=None, anchor="mm", fill='black')
        
        # Add track information
        try:
            # Try to use a larger font if available
            font_large = ImageFont.truetype("arial.ttf", 24)
            font_medium = ImageFont.truetype("arial.ttf", 18)
            font_small = ImageFont.truetype("arial.ttf", 14)
        except:
            # Fallback to default font
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        
        # Track name
        track_name = track_info.get('name', 'Unknown Track')
        if len(track_name) > 30:
            track_name = track_name[:27] + "..."
        draw.text((width//2, text_y), track_name, font=font_large, anchor="mt", fill='black')
        
        # Artist name
        artist_name = track_info.get('artist', 'Unknown Artist')
        if len(artist_name) > 40:
            artist_name = artist_name[:37] + "..."
        draw.text((width//2, text_y + 35), f"by {artist_name}", font=font_medium, anchor="mt", fill='gray')
        
        # Album name
        album_name = track_info.get('album', 'Unknown Album')
        if len(album_name) > 40:
            album_name = album_name[:37] + "..."
        draw.text((width//2, text_y + 65), f"from {album_name}", font=font_small, anchor="mt", fill='gray')
        
        # Progress bar
        if track_info.get('progress_ms') and track_info.get('duration_ms'):
            progress = track_info['progress_ms'] / track_info['duration_ms']
            bar_width = width - 100
            bar_height = 8
            bar_x = 50
            bar_y = text_y + 100
            
            # Background bar
            draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height], fill='lightgray', outline='gray')
            
            # Progress bar
            progress_width = int(bar_width * progress)
            draw.rectangle([bar_x, bar_y, bar_x + progress_width, bar_y + bar_height], fill='black')
            
            # Time stamps
            current_time = f"{track_info['progress_ms'] // 60000}:{(track_info['progress_ms'] // 1000) % 60:02d}"
            total_time = f"{track_info['duration_ms'] // 60000}:{(track_info['duration_ms'] // 1000) % 60:02d}"
            draw.text((bar_x, bar_y + bar_height + 5), current_time, font=font_small, fill='gray')
            draw.text((bar_x + bar_width, bar_y + bar_height + 5), total_time, font=font_small, anchor="rt", fill='gray')
        
        # Device info
        device = track_info.get('device', 'Unknown Device')
        draw.text((width//2, height - 30), f"Playing on {device}", font=font_small, anchor="mt", fill='gray')
        
        return image
    
    def create_no_music_image(self, width: int = 800, height: int = 480) -> Image.Image:
        """Create image when no music is playing"""
        image = Image.new('RGB', (width, height), color='white')
        draw = ImageDraw.Draw(image)
        
        try:
            font_large = ImageFont.truetype("arial.ttf", 36)
            font_medium = ImageFont.truetype("arial.ttf", 24)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
        
        # Draw large music note
        draw.text((width//2, height//2 - 50), "♪", font=font_large, anchor="mm", fill='lightgray')
        draw.text((width//2, height//2 + 20), "No music playing", font=font_medium, anchor="mm", fill='gray')
        draw.text((width//2, height//2 + 50), "Start playing on Spotify", font=font_medium, anchor="mm", fill='lightgray')
        
        return image
    
    # =========================================================================
    # Embedded Plugin Interface - Required for Mimir Plugin Architecture
    # =========================================================================
    
    def get_manifest(self) -> Dict[str, Any]:
        """Get channel manifest with capabilities"""
        try:
            current_track = self.get_current_track()
            spotify_cfg = self.config.get("spotify", {})
            configured = bool(spotify_cfg.get("client_id") and spotify_cfg.get("client_secret"))
            authorized = self.spotify_client is not None
            market = spotify_cfg.get("market")
            additional_types = spotify_cfg.get("additional_types")
            # Compute a simple content fingerprint for UI (mtime sum)
            try:
                ui_hash_parts = []
                if self.ui_dir.exists():
                    for p in self.ui_dir.glob('*'):
                        if p.is_file():
                            ui_hash_parts.append(str(int(p.stat().st_mtime)))
                ui_fingerprint = '-'.join(ui_hash_parts)
            except Exception:  # noqa: BLE001
                ui_fingerprint = 'unknown'
            
            return {
                "id": "com.spotify.status",
                "name": "Spotify Status Channel",
                "version": "1.0.0",
                "description": "Display currently playing Spotify track album art and metadata",
                "capabilities": {
                    "supports_upload": False,
                    "supports_gallery": False,
                    "supports_spotify": True,
                    "requires_auth": True,
                    "image_formats": ["jpg", "jpeg", "png"],
                    "max_file_size": "5MB"
                },
                "configuration": {
                    "configured": configured,
                    "authorized": authorized,
                    "redirect_uri": spotify_cfg.get("redirect_uri", "http://localhost:8080/callback"),
                    "client_id_present": bool(spotify_cfg.get("client_id")),
                    # Never expose secret value (only presence)
                    "client_secret_present": bool(spotify_cfg.get("client_secret")),
                    "market": market,
                    "additional_types": additional_types,
                },
                "ui": {
                    "entry_point": "/api/channels/com.spotify.status/ui/index.html",
                    "components": {
                        "manager": "/api/channels/com.spotify.status/ui/manage.esm.js"
                    },
                    # Explicit element names so host UI can mount correct custom elements
                    "elements": {
                        "manager": "x-spotify-status-manager",
                        "card": "x-spotify-status-card"
                    },
                    "styles": "/api/channels/com.spotify.status/ui/styles.css",
                    "icon": "🎵",
                    "title": "Spotify Status",
                    "fingerprint": ui_fingerprint
                },
                "current_track": current_track,
                "status": self.get_status(),
                "degraded": self.degraded,
                "dependencies": {"spotipy": _SPOTIPY_AVAILABLE}
            }
            
        except Exception as e:
            return {
                "id": "com.spotify.status",
                "name": "Spotify Status Channel",
                "error": str(e),
                "healthy": False
            }
    
    async def request_image(self, request_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate current track album art image"""
        try:
            # Parse display options
            options = request_data.get("options", {}) if request_data else {}
            width = options.get("width", 800)
            height = options.get("height", 480)
            spotify_cfg = self.config.get("spotify", {})
            if not (spotify_cfg.get("client_id") and spotify_cfg.get("client_secret")):
                return {
                    "success": False,
                    "error": "not_configured",
                    "message": "Spotify credentials not configured",
                }
            if not self.spotify_client:
                return {
                    "success": False,
                    "error": "not_authorized",
                    "message": "Spotify not authorized. Complete OAuth flow.",
                }
            
            # Get current track
            track_info = self.get_current_track()
            
            if track_info:
                # Create status image with album art
                image = self.create_status_image(track_info, width, height)
                track_name = track_info.get('name', 'Unknown Track')
                artist = track_info.get('artist', 'Unknown Artist')
                description = f"Now playing: {track_name} by {artist}"
            else:
                # Create "no music playing" image
                image = self.create_no_music_image(width, height)
                description = "No music currently playing on Spotify"
            
            # Convert to base64
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=95)
            image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return {
                "success": True,
                "image": image_base64,
                "format": "jpeg",
                "width": width,
                "height": height,
                "description": description,
                "track_info": track_info,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to generate image: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to generate Spotify status image: {str(e)}"
            }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current channel status"""
        try:
            spotify_connected = self.spotify_client is not None
            current_track = self.get_current_track()
            
            return {
                "active": True,
                "healthy": spotify_connected,
                "lastUpdate": datetime.now().isoformat(),
                "lastError": None,
                "version": "1.0.0",
                "spotify_connected": spotify_connected,
                "currently_playing": current_track is not None,
                "track_name": current_track.get('name') if current_track else None
            }
            
        except Exception as e:
            return {
                "active": False,
                "healthy": False,
                "lastUpdate": datetime.now().isoformat(),
                "lastError": str(e),
                "version": "1.0.0",
                "spotify_connected": False,
                "currently_playing": False
            }
    
    def get_router(self) -> APIRouter:
        """Get FastAPI router for custom endpoints"""
        router = APIRouter()
        logger.info("[SpotifyStatusChannel] Building router and mounting UI directory: %s", self.ui_dir)

        # ---------------- Settings Endpoints -----------------
        @router.get("/settings")
        async def get_settings():
            cfg = self.config.get("spotify", {})
            masked = {
                "client_id": cfg.get("client_id"),
                # Mask secret fully except last 4 chars
                "client_secret": ("***" + cfg.get("client_secret", "")[-4:]) if cfg.get("client_secret") else None,
                "redirect_uri": cfg.get("redirect_uri", "http://localhost:8080/callback"),
                "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
                "authorized": bool(self.spotify_client),
                "market": cfg.get("market"),
                "additional_types": cfg.get("additional_types"),
            }
            return JSONResponse({"success": True, "settings": masked})

        @router.post("/settings")
        async def update_settings(payload: Dict[str, Any]):
            spotify_cfg = self.config.setdefault("spotify", {})
            updated = False
            for key in ("client_id", "client_secret", "redirect_uri", "market", "additional_types"):
                if key in payload:
                    spotify_cfg[key] = payload[key]
                    updated = True
            if updated:
                self._save_settings(self.config)
                # Reinitialize client after updating credentials
                self._initialize_spotify_client()
            return JSONResponse({"success": True, "updated": updated})

        # ---------------- OAuth Initiation -----------------
        @router.get("/authorize")
        async def authorize():
            cfg = self.config.get("spotify", {})
            client_id = cfg.get("client_id")
            client_secret = cfg.get("client_secret")
            redirect_uri = cfg.get("redirect_uri", "http://localhost:8080/callback")
            if not client_id or not client_secret:
                raise HTTPException(status_code=400, detail="Spotify client_id and client_secret must be configured first")
            scope = "user-read-currently-playing user-read-playback-state"
            # Spotipy can build auth URL via helper
            try:
                auth_manager = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=scope,
                    cache_path=str(self.token_path),
                    show_dialog=True,
                )
                auth_url = auth_manager.get_authorize_url()
                return JSONResponse({"success": True, "authorize_url": auth_url})
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"Failed to build authorize URL: {e}")
        
        @router.get("/current-track")
        async def get_current_track_endpoint():
            """Get currently playing track metadata"""
            try:
                track_info = self.get_current_track()
                if track_info:
                    return JSONResponse({"success": True, "track": track_info})
                else:
                    return JSONResponse({"success": False, "message": "No track currently playing"})
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to get current track: {str(e)}")
        
        async def _handle_auth_callback(request: Request):
            """Internal helper for Spotify OAuth callback (GET/POST)."""
            if self.degraded:
                raise HTTPException(status_code=500, detail="Spotipy dependency missing; cannot authorize")
            try:
                params = dict(request.query_params)
                code = params.get("code")
                error = params.get("error")
                if error:
                    raise HTTPException(status_code=400, detail=f"Spotify authorization error: {error}")
                if not code:
                    raise HTTPException(status_code=400, detail="Missing authorization code")
                cfg = self.config.get("spotify", {})
                client_id = cfg.get("client_id")
                client_secret = cfg.get("client_secret")
                redirect_uri = cfg.get("redirect_uri", "http://localhost:8080/callback")
                scope = "user-read-currently-playing user-read-playback-state"
                if not (client_id and client_secret):
                    raise HTTPException(status_code=400, detail="Client credentials not configured")
                auth_manager = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=scope,
                    cache_path=str(self.token_path)
                )
                # Exchange code for token
                try:
                    token_info = auth_manager.get_access_token(code)  # type: ignore[arg-type]
                except TypeError:
                    # Some versions require arg name code
                    token_info = auth_manager.get_access_token(code=code)  # type: ignore[call-arg]
                if not token_info or 'access_token' not in token_info:
                    raise HTTPException(status_code=500, detail="Failed to obtain access token from Spotify")
                self.token_info = token_info
                self.spotify_client = spotipy.Spotify(auth_manager=auth_manager)
                logger.info("Spotify authorization completed; token expires at %s", token_info.get('expires_at'))
                return JSONResponse({
                    "success": True,
                    "message": "Spotify authorized",
                    "connected": True,
                    "expires_at": token_info.get('expires_at')
                })
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"Auth callback failed: {str(e)}")

        # Support both GET and POST for callback (Spotify typically uses GET redirect)
        @router.get("/auth/callback")
        async def spotify_auth_callback_get(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        @router.post("/auth/callback")
        async def spotify_auth_callback_post(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        @router.get("/authorize/status")
        async def authorize_status():  # noqa: D401
            return JSONResponse({
                "authorized": self.spotify_client is not None,
                "degraded": self.degraded,
                "token_expires_at": (self.token_info or {}).get('expires_at') if self.token_info else None
            })

        # ---------------- Core Channel Contract Endpoints (for API docs visibility) -----------------
        @router.get("/manifest", summary="Channel manifest")
        async def manifest_endpoint():
            return JSONResponse(self.get_manifest())

        @router.post("/request_image", summary="Generate Spotify status image")
        async def request_image_endpoint(payload: Dict[str, Any] = None):  # noqa: D401
            result = await self.request_image(payload or {})
            return JSONResponse(result)

        @router.get("/health", summary="Health check")
        async def health_endpoint():  # noqa: D401
            return JSONResponse({"success": True, "status": self.get_status()})

        @router.get("/dependencies", summary="Dependency status")
        async def dependencies_endpoint():  # noqa: D401
            return JSONResponse({
                "spotipy_available": _SPOTIPY_AVAILABLE,
                "degraded": self.degraded,
                "expected": [
                    "spotipy>=2.22.1",
                    "Pillow>=10.0.0",
                    "requests>=2.31.0",
                    "fastapi>=0.100.0"
                ]
            })

        # --- Feature detection support (frontend calls /test to probe channel capabilities) ---
        # Support both POST and GET because some frontend code may probe with GET
        def _test_payload() -> Dict[str, Any]:
            return {
                "success": True,
                "id": "com.spotify.status",
                "message": "Spotify channel responsive",
                "degraded": self.degraded,
                "authorized": self.spotify_client is not None
            }

        @router.post("/test", summary="Channel test endpoint (POST)")
        async def test_post_endpoint():  # noqa: D401
            return JSONResponse(_test_payload())

        @router.get("/test", summary="Channel test endpoint (GET)")
        async def test_get_endpoint():  # noqa: D401
            return JSONResponse(_test_payload())

        # --- Compatibility: some frontend code may expect /image_request instead of /request_image ---
        @router.post("/image_request", summary="Generate image (compat)")
        async def request_image_compat(payload: Dict[str, Any] = None):  # noqa: D401
            result = await self.request_image(payload or {})
            # Indicate this came via compatibility route for debugging
            result["compat_endpoint"] = True
            return JSONResponse(result)
        
        # Mount UI static files (primary) + explicit fallbacks for environments that do not honor router.mount
        try:
            router.mount("/ui", StaticFiles(directory=str(self.ui_dir)), name="ui")
            logger.info("[SpotifyStatusChannel] Mounted /ui static directory")
        except Exception as e:  # noqa: BLE001
            logger.warning("[SpotifyStatusChannel] Failed to mount static /ui directory: %s", e)

        # --- Fallback explicit asset endpoints (in case platform doesn't use mounted static routes) ---
        @router.get("/ui/index.esm.js")
        async def ui_index_js():  # noqa: D401
            """Serve dashboard card module (fallback)."""
            path = self.ui_dir / "index.esm.js"
            if not path.exists():
                raise HTTPException(status_code=404, detail="index.esm.js not found")
            return FileResponse(str(path), media_type="text/javascript")

        @router.get("/ui/manage.esm.js")
        async def ui_manage_js():  # noqa: D401
            """Serve manager module (fallback)."""
            path = self.ui_dir / "manage.esm.js"
            if not path.exists():
                raise HTTPException(status_code=404, detail="manage.esm.js not found")
            return FileResponse(str(path), media_type="text/javascript")

        @router.get("/ui/styles.css")
        async def ui_styles():  # noqa: D401
            """Serve UI stylesheet (fallback)."""
            path = self.ui_dir / "styles.css"
            if not path.exists():
                raise HTTPException(status_code=404, detail="styles.css not found")
            return FileResponse(str(path), media_type="text/css")

        @router.get("/ui/index.html")
        async def ui_index_html():  # noqa: D401
            """Serve standalone manager HTML (fallback)."""
            path = self.ui_dir / "index.html"
            if not path.exists():
                raise HTTPException(status_code=404, detail="index.html not found")
            return FileResponse(str(path), media_type="text/html")

        # ---------------- Debug Helpers -----------------
        @router.get("/ui/debug-list")
        async def ui_debug_list():
            if not self.ui_dir.exists():
                return JSONResponse({"exists": False, "path": str(self.ui_dir)})
            files = []
            for p in sorted(self.ui_dir.glob('*')):
                files.append({
                    "name": p.name,
                    "is_file": p.is_file(),
                    "size": p.stat().st_size if p.is_file() else None,
                    "mtime": p.stat().st_mtime
                })
            return JSONResponse({
                "exists": True,
                "path": str(self.ui_dir),
                "count": len(files),
                "files": files
            })

        @router.get("/ui/ping")
        async def ui_ping():
            return JSONResponse({"ok": True, "message": "spotify ui router active"})
        
        return router


# Export the channel class for embedded plugin discovery
ChannelClass = SpotifyStatusChannel
