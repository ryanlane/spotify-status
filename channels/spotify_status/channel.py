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
import threading
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from PIL import Image, ImageDraw, ImageFont
from .renderer import PillowRenderer, RenderOptions
from .service import SpotifyService
from .models import TrackInfo
from .push import PushManager

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

# Attempt optional imports for HTML rendering pathway
try:  # HTML template rendering
    from jinja2 import Environment, FileSystemLoader, select_autoescape  # type: ignore
    _JINJA2_AVAILABLE = True
except Exception:  # noqa: BLE001
    _JINJA2_AVAILABLE = False

try:  # Headless browser screenshot
    from playwright.sync_api import sync_playwright  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _PLAYWRIGHT_AVAILABLE = False

# (logging already configured above)


class SpotifyStatusChannel:
    """
    Spotify Status Channel - Embedded Plugin for Mimir Platform
    
    Fetches currently playing track from Spotify and generates album art images
    with optional metadata overlay for e-paper display.
    """
    
    def __init__(self, config: Any):  # Accept Any to be robust against loader passing a path string
        """Initialize Spotify Status Channel.

        The plugin discovery layer currently instantiates channel classes by passing the plugin
        directory path as a string. Original implementation expected a dict and would therefore
        raise an AttributeError when calling dict methods on a str, causing manifest generation
        to fail and the API to return 404 for /manifest. We normalize the incoming value here.

        Supported constructor inputs now:
        - dict: treated as full config object (legacy / future explicit config)
        - str or Path: treated as root path; config scaffold created
        - None: empty default config scaffold
        """
        # Normalize input into a dict structure: { 'root_path': <path>, 'spotify': { ... } }
        normalized: Dict[str, Any]
        if isinstance(config, dict):
            normalized = config
        elif isinstance(config, (str, Path)):
            normalized = {"root_path": str(config), "spotify": {}}
        elif config is None:
            normalized = {"spotify": {}}
        else:
            # Last-resort fallback to avoid breaking initialization
            normalized = {"unexpected_config_type": str(type(config)), "spotify": {}}
        self.config = normalized
        self.channel_dir = Path(__file__).parent
        self.data_dir = self.channel_dir / "data"
        self.ui_dir = self.channel_dir / "ui"
        self.settings_path = self.data_dir / "settings.json"
        self.token_path = self.data_dir / ".spotify_cache"  # spotipy cache path
        self.degraded = not _SPOTIPY_AVAILABLE
        # HTML template support attributes (initialized lazily)
        self.templates_dir = self.ui_dir / "templates"
        self._template_env = None  # lazy Jinja2 env
        
        # Ensure directories exist
        self.data_dir.mkdir(exist_ok=True)
        
        # Spotify API configuration
        self.spotify_client = None
        # Holds OAuth token metadata (expires_at, access_token, refresh_token, etc.) once authorized
        self.token_info = None  # type: Optional[Dict[str, Any]]
        self.last_track_cache = None
        self.cache_timestamp = None
        self.cache_duration = 30  # seconds
        # Renderer instance (can be swapped with alternate implementations later)
        self._renderer = PillowRenderer()

        # --- Push / Event streaming support (poll + dispatch) ---
        # We present a very light-weight push abstraction so the host API service
        # can simply call `register_listener(callback)` to receive track change events
        # without needing to schedule polling jobs for this channel.
        # This internally still performs short-interval polling (Spotify offers no
        # first-party server push), but centralizes it within the plugin and only
        # emits events when something meaningful changes.
        self.supports_push = True  # Capability flag exposed in manifest
        self.push_poll_interval = (
            int(self.config.get("spotify", {}).get("push_poll_interval", 10))
            if isinstance(self.config.get("spotify", {}).get("push_poll_interval", 10), (int, float))
            else 10
        )  # seconds between background checks
        if self.push_poll_interval < 3:
            # Hard floor to avoid API spam / rate-limit risk
            self.push_poll_interval = 3

        # Optional outbound webhook (POST) – configured via settings.json ("webhook_url")
        self.webhook_url: Optional[str] = self.config.get("spotify", {}).get("webhook_url")

        # Push manager abstraction (replaces raw thread + listener lists)
        self._push_manager: Optional[PushManager] = None

        # Load persisted settings if present and merge with provided config
        persisted = self._load_settings()
        try:
            if persisted:
                # Merge persisted settings with provided config (provided wins)
                base_spotify_cfg = self.config.get("spotify", {}) or {}
                persisted_spotify_cfg = persisted.get("spotify", {}) or {}
                merged = {**persisted_spotify_cfg, **base_spotify_cfg}
                # Auto-upgrade legacy redirect URIs to new canonical host 127.0.0.1:5000
                legacy_redirects = {
                    "http://localhost:8080/api/channels/com.spotify.status/callback",
                    "http://localhost:5000/api/channels/com.spotify.status/callback",
                }
                canonical_redirect = "http://127.0.0.1:5000/api/channels/com.spotify.status/callback"
                if merged.get("redirect_uri") in legacy_redirects or merged.get("redirect_uri") is None:
                    merged["redirect_uri"] = canonical_redirect
                self.config["spotify"] = merged
            else:
                # Persist initial scaffold so subsequent restarts retain structure
                self._save_settings(self.config)
        except Exception as merge_e:  # noqa: BLE001
            logger.warning("[SpotifyStatusChannel] Failed merging persisted settings: %s", merge_e)
        
        # Initialize Spotify client
        if not self.degraded:
            self._initialize_spotify_client()
            # Start push thread early (it will block silently until authorized)
            self._ensure_push_manager()
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
            # New default redirect URI path (versioned change from legacy /callback root)
            redirect_uri = spotify_config.get(
                "redirect_uri",
                "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
            )
            
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
                    # Defer import to avoid circular during module load (already imported at top, safe reuse)
                    from .service import SpotifyService  # local import for clarity
                    self.spotify_service = SpotifyService(self.spotify_client, cache_ttl=self.cache_duration)
                    logger.info("Spotify client initialized from cached token (service ready)")
                else:
                    # Not yet authorized; keep auth_manager handy for later callback use
                    self.spotify_client = None
                    self.spotify_service = None
                    logger.info("Spotify client not yet authorized (no cached token)")
            except Exception as cache_e:  # noqa: BLE001
                logger.debug(f"[SpotifyStatusChannel] No cached token available: {cache_e}")
                self.spotify_client = None
                self.spotify_service = None

            # If we become authorized later (after credential update), make sure push loop is active
            self._ensure_push_thread()
            
        except Exception as e:
            logger.error(f"Failed to initialize Spotify client: {e}")
            self.spotify_client = None
    
    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Return current track info via SpotifyService (dict form)."""
        if not self.spotify_service:
            return None
        cfg = self.config.get("spotify", {})
        market = cfg.get("market") or None
        additional_types = cfg.get("additional_types") or None
        if additional_types:
            cleaned: List[str] = []
            for t in str(additional_types).split(','):
                t = t.strip().lower()
                if t in ("track", "episode") and t not in cleaned:
                    cleaned.append(t)
            additional_types = ",".join(cleaned) if cleaned else None
        track = self.spotify_service.get_current_track(market=market, additional_types=additional_types)  # type: ignore[arg-type]
        if track:
            data = track.to_dict()
            self.last_track_cache = data
            self.cache_timestamp = datetime.now()
            return data
        return None
    
    # Removed image rendering helper methods; now delegated to PillowRenderer
    
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
                    "max_file_size": "5MB",
                    # --- Push extension fields ---
                    "update_modes": ["scheduler", "push"],
                    "preferred_mode": "push",
                    "push_supported": True,
                    "push_event_types": ["now_playing_changed", "playback_state_changed"],
                    "push_poll_interval": self.push_poll_interval
                },
                "configuration": {
                    "configured": configured,
                    "authorized": authorized,
                    "redirect_uri": spotify_cfg.get(
                        "redirect_uri",
                        "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
                    ),
                    "client_id_present": bool(spotify_cfg.get("client_id")),
                    # Never expose secret value (only presence)
                    "client_secret_present": bool(spotify_cfg.get("client_secret")),
                    "market": market,
                    "additional_types": additional_types,
                    "render_mode": spotify_cfg.get("render_mode", "pillow"),
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
                "dependencies": {"spotipy": _SPOTIPY_AVAILABLE},
                "push": {
                    "active": self._push_thread is not None and self._push_thread.is_alive(),
                    "listener_count": len(self._listeners),
                    "webhook_configured": bool(self.webhook_url)
                }
            }
            
        except Exception as e:
            return {
                "id": "com.spotify.status",
                "name": "Spotify Status Channel",
                "error": str(e),
                "healthy": False
            }

    def get_status(self) -> Dict[str, Any]:
        """Return lightweight health/status snapshot.

        Exposed via /health and embedded in manifest. Pulled into its own method
        to keep `get_manifest` smaller and enable reuse by potential future
        monitoring endpoints.
        """
        return {
            "degraded": self.degraded,
            "authorized": self.spotify_client is not None,
            "cache_age_sec": (datetime.now() - self.cache_timestamp).total_seconds() if self.cache_timestamp else None,
            "listeners": len(self._listeners),
            "push_thread": (self._push_thread.is_alive() if self._push_thread else False),
            "poll_interval": self.push_poll_interval,
        }
    
    async def request_image(self, request_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate current track album art image.

        Updated for new embedded channel image pipeline:
        - Return raw image bytes under the "bytes" key (not base64) so the host API
          can store and serve via /api/channels/{id}/images/{image_id} without a
          base64 round‑trip.
        - Base64 is only included if the caller explicitly sets include_base64=True
          (or for backward compatibility if omit_base64=False was not passed and
          include_base64 not provided – default now is to suppress to reduce payload size).
        - Retains legacy "image" field (base64) only when included so older UIs do
          not break if directly invoking plugin endpoint.
        """
        steps: list[str] = []
        reason: Optional[str] = None
        try:
            steps.append("parse_options")
            options = request_data.get("options", {}) if request_data else {}
            width = int(options.get("width", 800) or 800)
            height = int(options.get("height", 480) or 480)
            render_mode = (options.get("render_mode") or self.config.get("spotify", {}).get("render_mode") or "pillow").lower()
            grayscale_flag = bool(options.get("grayscale", False))

            # Flags controlling response shaping
            include_base64_flag = bool(
                (request_data or {}).get("include_base64", False)
            )
            suppress_legacy = bool(
                (request_data or {}).get("suppress_legacy_base64", not include_base64_flag)
            )

            spotify_cfg = self.config.get("spotify", {})
            if not (spotify_cfg.get("client_id") and spotify_cfg.get("client_secret")):
                reason = "not_configured"
                return {
                    "success": False,
                    "error": "Spotify credentials not configured",
                    "reason": reason,
                    "steps": steps,
                }
            if not self.spotify_client:
                reason = "not_authorized"
                return {
                    "success": False,
                    "error": "Spotify not authorized. Complete OAuth flow.",
                    "reason": reason,
                    "steps": steps,
                }

            steps.append("get_current_track")
            track_info = self.get_current_track()

            pre_encoded_bytes: Optional[bytes] = None
            out_format = 'jpeg'
            content_type = 'image/jpeg'

            if render_mode == "html":
                steps.append("html_mode_selected")
                if not self._supports_html_rendering():
                    steps.append("html_mode_unavailable_fallback")
                else:
                    try:
                        steps.append("html_render_start")
                        pre_encoded_bytes = await self._render_html_image(track_info, width, height, options)
                        if pre_encoded_bytes:
                            steps.append("html_render_success")
                            out_format = 'png'  # Playwright screenshot default
                            content_type = 'image/png'
                        else:
                            steps.append("html_render_returned_none_fallback")
                    except Exception as html_e:  # noqa: BLE001
                        logger.warning("[SpotifyStatusChannel] HTML render failed, falling back to pillow: %s", html_e)
                        steps.append("html_render_exception_fallback")

            steps.append("choose_template")
            if pre_encoded_bytes is None:
                if track_info:
                    steps.append("create_status_image")
                    image = self._renderer.create_status_image(track_info, RenderOptions(width=width, height=height, grayscale=grayscale_flag))
                    track_name = track_info.get('name', 'Unknown Track')
                    artist = track_info.get('artist', 'Unknown Artist')
                    description = f"Now playing: {track_name} by {artist}"
                else:
                    steps.append("create_no_music_image")
                    image = self._renderer.create_no_music_image(RenderOptions(width=width, height=height, grayscale=grayscale_flag))
                    description = "No music currently playing on Spotify"

                if grayscale_flag:
                    try:
                        steps.append("apply_grayscale")
                        image = image.convert("L")
                    except Exception:  # noqa: BLE001
                        steps.append("grayscale_failed")
                steps.append("encode_image")
                buffer = io.BytesIO()
                try:
                    image.save(buffer, format='JPEG', quality=95)
                except Exception as jpeg_err:  # noqa: BLE001
                    logger.warning("JPEG encode failed (%s); falling back to PNG", jpeg_err)
                    steps.append("jpeg_fallback_png")
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG')
                    out_format = 'png'
                    content_type = 'image/png'
                raw_bytes = buffer.getvalue()
            else:
                # pre-encoded PNG from HTML path
                raw_bytes = pre_encoded_bytes
                description = "HTML rendered Spotify status" if track_info else "HTML rendered no music screen"
                if grayscale_flag:
                    try:
                        steps.append("apply_grayscale_html")
                        tmp_img = Image.open(io.BytesIO(raw_bytes))
                        tmp_img = tmp_img.convert("L")
                        buf2 = io.BytesIO()
                        tmp_img.save(buf2, format='PNG')
                        raw_bytes = buf2.getvalue()
                        out_format = 'png'
                        content_type = 'image/png'
                    except Exception:  # noqa: BLE001
                        steps.append("grayscale_html_failed")

            # ...existing code for base64 encode and return...
            image_base64: Optional[str] = None
            if include_base64_flag and not suppress_legacy:
                steps.append("encode_base64")
                image_base64 = base64.b64encode(raw_bytes).decode('utf-8')

            steps.append("return_success")
            result: Dict[str, Any] = {
                "success": True,
                "format": out_format,
                "width": width,
                "height": height,
                "description": description,
                "track_info": track_info,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "steps": steps,
                "bytes": raw_bytes,
                "content_type": content_type,
                "preferred_transport": "bytes",
                "render_mode": render_mode,
                "grayscale": grayscale_flag,
            }
            if image_base64 is not None:
                result["image"] = image_base64
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[SpotifyStatusChannel] Failed to generate image at step %s: %s", steps[-1] if steps else 'start', e)
            reason = reason or "generation_error"
            return {
                "success": False,
                "error": str(e) or "unknown",
                "reason": reason,
                "message": "Failed to generate Spotify status image",
                "steps": steps,
                "exception_type": type(e).__name__,
            }
    
    # ---------------- HTML Rendering Helpers ----------------
    def _supports_html_rendering(self) -> bool:
        return _JINJA2_AVAILABLE and _PLAYWRIGHT_AVAILABLE and self.templates_dir.exists()

    def _get_template_env(self):
        if self._template_env is None and _JINJA2_AVAILABLE and self.templates_dir.exists():
            try:
                self._template_env = Environment(
                    loader=FileSystemLoader(str(self.templates_dir)),
                    autoescape=select_autoescape(["html", "xml"]),
                    enable_async=False,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[SpotifyStatusChannel] Failed to init Jinja2 env: %s", e)
                self._template_env = None
        return self._template_env

    async def _render_html_image(self, track_info: Optional[Dict[str, Any]], width: int, height: int, options: Dict[str, Any]) -> Optional[bytes]:
        if not self._supports_html_rendering():
            return None
        env = self._get_template_env()
        if env is None:
            return None
        template_name = "now_playing.html"
        if not (self.templates_dir / template_name).exists():
            return None
        theme = (options.get("theme") or "light").lower()
        ctx = self._build_template_context(track_info, width, height, theme)
        # Render HTML synchronously then screenshot in a worker thread to avoid blocking loop
        def _do_render() -> Optional[bytes]:  # blocking
            try:
                html = env.get_template(template_name).render(**ctx)
                with sync_playwright() as p:  # type: ignore
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.set_content(html, wait_until="networkidle")
                    # Full page screenshot (already sized to viewport)
                    png_bytes = page.screenshot(type="png")
                    browser.close()
                    return png_bytes
            except Exception as e:  # noqa: BLE001
                logger.debug("[SpotifyStatusChannel] _render_html_image error: %s", e)
                return None
        return await asyncio.to_thread(_do_render)

    def _build_template_context(self, track_info: Optional[Dict[str, Any]], width: int, height: int, theme: str) -> Dict[str, Any]:
        if not track_info:
            return {
                "has_track": False,
                "width": width,
                "height": height,
                "theme": theme,
                "message": "No music playing",
            }
        duration = track_info.get("duration_ms") or 0
        progress = track_info.get("progress_ms") or 0
        pct = (progress / duration * 100) if duration else 0
        return {
            "has_track": True,
            "width": width,
            "height": height,
            "theme": theme,
            "track_name": track_info.get("name"),
            "artist_name": track_info.get("artist"),
            "album_name": track_info.get("album"),
            "album_art_url": track_info.get("album_art_url"),
            "is_playing": track_info.get("is_playing"),
            "progress_ms": progress,
            "duration_ms": duration,
            "progress_pct": pct,
            "device": track_info.get("device"),
        }

    # ...existing code...
    def get_router(self) -> APIRouter:  # existing method already starts here earlier; we patch inside endpoints
        """Get FastAPI router for custom endpoints"""
        router = APIRouter()
        logger.info("[SpotifyStatusChannel] Building router and mounting UI directory: %s", self.ui_dir)

        # ---------------- Settings Endpoints -----------------
        @router.get("/settings")
        async def get_settings():
            cfg = self.config.get("spotify", {})
            masked = {
                "client_id": cfg.get("client_id"),
                "client_secret": ("***" + cfg.get("client_secret", "")[-4:]) if cfg.get("client_secret") else None,
                "redirect_uri": cfg.get(
                    "redirect_uri",
                    "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
                ),
                "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
                "authorized": bool(self.spotify_client),
                "market": cfg.get("market"),
                "additional_types": cfg.get("additional_types"),
                "render_mode": cfg.get("render_mode", "pillow"),
            }
            return JSONResponse({"success": True, "settings": masked})

        @router.post("/settings")
        async def update_settings(payload: Dict[str, Any]):
            spotify_cfg = self.config.setdefault("spotify", {})
            updated = False
            for key in ("client_id", "client_secret", "redirect_uri", "market", "additional_types", "render_mode"):
                if key in payload:
                    spotify_cfg[key] = payload[key]
                    updated = True
            if updated:
                self._save_settings(self.config)
                # Reinitialize client only if credentials touched
                if any(k in payload for k in ("client_id", "client_secret")):
                    self._initialize_spotify_client()
            return JSONResponse({"success": True, "updated": updated})

        # ---------------- OAuth Initiation -----------------
        @router.get("/authorize")
        async def authorize():
            cfg = self.config.get("spotify", {})
            client_id = cfg.get("client_id")
            client_secret = cfg.get("client_secret")
            redirect_uri = cfg.get(
                "redirect_uri",
                "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
            )
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
                # Primary (stored) redirect URI
                redirect_uri = cfg.get(
                    "redirect_uri",
                    "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
                )
                # Fallback legacy/canonical variants we are willing to attempt if initial exchange
                # fails with redirect mismatch related errors.
                redirect_fallbacks = [
                    redirect_uri,
                    # legacy localhost variants
                    "http://localhost:5000/api/channels/com.spotify.status/callback",
                    "http://localhost:8080/api/channels/com.spotify.status/callback",
                    # host-only variant sometimes entered by user
                    "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
                ]
                # De-duplicate while preserving order
                seen = set()
                redirect_fallbacks = [r for r in redirect_fallbacks if not (r in seen or seen.add(r))]
                scope = "user-read-currently-playing user-read-playback-state"
                if not (client_id and client_secret):
                    raise HTTPException(status_code=400, detail="Client credentials not configured")
                last_error: Optional[str] = None
                for attempt_idx, attempted_redirect in enumerate(redirect_fallbacks, start=1):
                    try:
                        auth_manager = SpotifyOAuth(
                            client_id=client_id,
                            client_secret=client_secret,
                            redirect_uri=attempted_redirect,
                            scope=scope,
                            cache_path=str(self.token_path)
                        )
                        try:
                            token_info = auth_manager.get_access_token(code)  # type: ignore[arg-type]
                        except TypeError:
                            token_info = auth_manager.get_access_token(code=code)  # type: ignore[call-arg]
                        if token_info and token_info.get('access_token'):
                            # Success: persist canonical redirect if it differs
                            if attempted_redirect != cfg.get("redirect_uri"):
                                cfg["redirect_uri"] = attempted_redirect
                                self._save_settings(self.config)
                            self.token_info = token_info
                            self.spotify_client = spotipy.Spotify(auth_manager=auth_manager)
                            logger.info(
                                "Spotify authorization completed on attempt %d using redirect %s; token expires at %s",
                                attempt_idx,
                                attempted_redirect,
                                token_info.get('expires_at'),
                            )
                            return JSONResponse({
                                "success": True,
                                "message": "Spotify authorized",
                                "connected": True,
                                "redirect_used": attempted_redirect,
                                "attempts": attempt_idx,
                                "expires_at": token_info.get('expires_at')
                            })
                        last_error = "Missing access token in response"
                    except Exception as ex:  # noqa: BLE001
                        last_error = str(ex)
                        # Continue to next fallback
                        continue
                raise HTTPException(status_code=500, detail=f"Auth callback failed after trying {len(redirect_fallbacks)} redirect(s): {last_error}")
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"Auth callback failed: {str(e)}")

        # Support both GET and POST for callback (Spotify typically uses GET redirect)
        # Primary (new) callback endpoint (preferred)
        @router.get("/callback")
        async def spotify_auth_callback_new_get(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        @router.post("/callback")
        async def spotify_auth_callback_new_post(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        # Backward compatibility with earlier implementation using /auth/callback
        @router.get("/auth/callback")
        async def spotify_auth_callback_legacy_get(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        @router.post("/auth/callback")
        async def spotify_auth_callback_legacy_post(request: Request):  # noqa: D401
            return await _handle_auth_callback(request)

        @router.get("/authorize/status")
        async def authorize_status():  # noqa: D401
            return JSONResponse({
                "authorized": self.spotify_client is not None,
                "degraded": self.degraded,
                "token_expires_at": (self.token_info or {}).get('expires_at') if self.token_info else None
            })

        @router.get("/authorize/redirects")
        async def authorize_redirects():  # noqa: D401
            cfg = self.config.get("spotify", {})
            current = cfg.get("redirect_uri")
            candidates = [
                current,
                "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
                "http://localhost:5000/api/channels/com.spotify.status/callback",
                "http://localhost:8080/api/channels/com.spotify.status/callback",
            ]
            # Deduplicate
            seen = set()
            ordered = [c for c in candidates if c and not (c in seen or seen.add(c))]
            return JSONResponse({
                "success": True,
                "current": current,
                "candidates": ordered,
                "note": "Order reflects fallback attempt priority in callback handler"
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
                ],
                "push": {
                    "supports_push": self.supports_push,
                    "poll_interval": self.push_poll_interval,
                    "active": self._push_thread is not None and self._push_thread.is_alive(),
                    "listeners": len(self._listeners),
                    "webhook_url": bool(self.webhook_url)
                }
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

        # --------------- Push Event Helper Endpoints ---------------
        @router.get("/push/status")
        async def push_status():  # noqa: D401
            return JSONResponse({
                "supports_push": self.supports_push,
                "thread_alive": self._push_thread.is_alive() if self._push_thread else False,
                "listeners": len(self._listeners),
                "poll_interval": self.push_poll_interval,
                "webhook": bool(self.webhook_url)
            })

        @router.post("/push/trigger")
        async def push_trigger():  # noqa: D401
            # Manual trigger for debugging; forces immediate poll + dispatch if changed
            changed = self._poll_and_maybe_emit(force=True)
            return JSONResponse({"forced": True, "emitted": changed})
        
        return router

    # =========================================================================
    # Push / Event Streaming Implementation
    # =========================================================================
    def register_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register a listener to receive channel events.

        A listener is a callable that accepts an event dict. It should be fast / non-blocking.
        If async work is needed, wrap with `lambda e: asyncio.create_task(coro(e))` externally.
        """
        with self._listeners_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)
        self._ensure_push_thread()

    def unregister_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._listeners_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _ensure_push_thread(self):
        """Start the background polling thread if not already running."""
        if not self.supports_push:
            return
        if self._push_thread is not None and self._push_thread.is_alive():
            return
        # Fresh stop event
        self._push_thread_stop.clear()
        self._push_thread = threading.Thread(target=self._push_loop, name="SpotifyPushLoop", daemon=True)
        self._push_thread.start()
        logger.info("[SpotifyStatusChannel] Push loop thread started (interval=%ss)", self.push_poll_interval)

    def _push_loop(self):
        """Background thread loop that polls and emits events when state changes."""
        while not self._push_thread_stop.is_set():
            try:
                self._poll_and_maybe_emit()
                self._consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.warning("[SpotifyStatusChannel] Push loop error: %s", exc)
                else:
                    # After repeated failures, back off more aggressively
                    logger.error("[SpotifyStatusChannel] Repeated push loop errors (%d): %s", self._consecutive_errors, exc)
            # Dynamic sleep (longer if repeated errors)
            backoff = min(self._consecutive_errors * 2, 30)
            time.sleep(self.push_poll_interval + backoff)

    def _poll_and_maybe_emit(self, force: bool = False) -> bool:
        """Poll Spotify and emit an event if there is a meaningful change.

        Returns True if an event was emitted.
        """
        if not self.spotify_client:
            return False  # Not authorized yet
        track_info = self.get_current_track()
        if not track_info:
            return False
        track_id = track_info.get("track_id")
        is_playing = track_info.get("is_playing")
        # Build a lightweight change fingerprint
        event_hash = f"{track_id}:{'1' if is_playing else '0'}"
        changed_track = track_id is not None and track_id != self._last_event_track_id
        changed_play_state = is_playing != self._last_event_is_playing
        if not force and not (changed_track or changed_play_state):
            return False

        event_type = "now_playing_changed" if changed_track else "playback_state_changed"
        event = {
            "channel_id": "com.spotify.status",
            "event_type": event_type,
            "payload": track_info,
            "ts": time.time(),
            "version": 1,
            "hash": event_hash,
        }
        self._last_event_track_id = track_id
        self._last_event_is_playing = is_playing  # type: ignore[assignment]
        self._last_event_hash = event_hash
        self._dispatch_event(event)
        return True

    def _dispatch_event(self, event: Dict[str, Any]) -> None:
        """Send event to registered listeners and optional webhook."""
        # Copy listeners snapshot to avoid holding lock during callbacks
        with self._listeners_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SpotifyStatusChannel] Listener raised: %s", exc)
        if self.webhook_url:
            try:
                # Fire-and-forget POST (ignore response body)
                requests.post(self.webhook_url, json=event, timeout=5)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[SpotifyStatusChannel] Webhook POST failed: %s", exc)

    def stop(self):  # Optional public stop hook
        if self._push_thread and self._push_thread.is_alive():
            self._push_thread_stop.set()
            logger.info("[SpotifyStatusChannel] Stopping push loop thread...")


# Export the channel class for embedded plugin discovery
ChannelClass = SpotifyStatusChannel
