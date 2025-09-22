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
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
import sys
import importlib.util
from types import ModuleType
from PIL import Image  # type: ignore

# ---------------------------------------------------------------------------
_PLUGIN_DIR = Path(__file__).parent
if str(_PLUGIN_DIR) not in sys.path:
    # Append so we do not shadow other plugins' generic module names (e.g. models)
    sys.path.append(str(_PLUGIN_DIR))

def _import_local(module_name: str, file_name: Optional[str] = None) -> ModuleType:
    """Import a sibling module by explicit path, avoiding fragile relative imports.

    This bypasses Python's package-relative resolution so the plugin can be
    safely loaded via importlib with any synthetic module name.
    """
    target = _PLUGIN_DIR / (file_name or f"{module_name}.py")
    if not target.exists():
        raise ImportError(f"Local module file not found: {target}")
    unique_name = f"spotify_status_{module_name}"
    spec = importlib.util.spec_from_file_location(unique_name, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {target}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[assignment]
    except Exception as e:  # noqa: BLE001
        raise ImportError(f"Failed executing {target}: {e}") from e
    return module

# Load local modules explicitly (no relative import usage)
renderer_mod = _import_local("renderer")
PillowRenderer = getattr(renderer_mod, "PillowRenderer")
RenderOptions = getattr(renderer_mod, "RenderOptions")

svg_renderer_mod = _import_local("svg_renderer")
SvgRenderer = getattr(svg_renderer_mod, "SvgRenderer")

service_mod = _import_local("service")
SpotifyService = getattr(service_mod, "SpotifyService")

models_mod = _import_local("models", file_name="models.py") if (_PLUGIN_DIR / "models.py").exists() else _import_local("models")
TrackInfo = getattr(models_mod, "TrackInfo")

push_mod = _import_local("push")
PushManager = getattr(push_mod, "PushManager")

_SPOTIPY_AVAILABLE = False
spotipy = None  # type: ignore
SpotifyOAuth = None  # type: ignore

# We need logging configured before we can safely log missing dependency status
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Remove generic module names that may pollute global import space if an older
# version of this plugin was previously loaded (pre-sandbox). This prevents
# other plugins that do 'import models' from accidentally binding to our
# TrackInfo-only module instead of their own richer models module.
_possibly_conflicting = ["models", "service", "renderer", "push", "svg_renderer"]
for _name in _possibly_conflicting:
    _m = sys.modules.get(_name)
    try:
        if _m and getattr(_m, "__file__", "").startswith(str(_PLUGIN_DIR)):
            del sys.modules[_name]
            logger.debug("[SpotifyStatusChannel] Removed stray global module alias '%s'", _name)
    except Exception:  # noqa: BLE001
        pass

try:  # Optional dependency guard so router still mounts even if spotipy missing
    import spotipy  # type: ignore
    from spotipy.oauth2 import SpotifyOAuth  # type: ignore
    _SPOTIPY_AVAILABLE = True
except ImportError:  # noqa: BLE001
    logger.warning("[SpotifyStatusChannel] 'spotipy' not installed – channel running in degraded mode (no Spotify API calls)")
from fastapi import APIRouter

# Attempt optional imports for HTML rendering pathway
_JINJA2_AVAILABLE = False  # HTML rendering pathway removed
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
    # (HTML template rendering removed; previously set templates_dir/_template_env)
        
        # Ensure directories exist
        self.data_dir.mkdir(exist_ok=True)
        
        # Spotify API configuration
        self.spotify_client = None
        # Holds OAuth token metadata (expires_at, access_token, refresh_token, etc.) once authorized
        self.token_info = None  # type: Optional[Dict[str, Any]]
        self.last_track_cache = None
        self.cache_timestamp = None
        self.cache_duration = 30  # seconds
        # Renderers
        self._renderer = PillowRenderer()
        self._svg_renderer = SvgRenderer(self.channel_dir / "svg")

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
        # Defer type hint (dynamic import); simple assignment avoids runtime parser complaints
        self._push_manager = None  # type: ignore[attr-defined]

        # Load persisted settings if present and merge with provided config
        persisted = self._load_settings()
        try:
            if persisted:
                base_spotify_cfg = self.config.get("spotify", {}) or {}
                persisted_spotify_cfg = persisted.get("spotify", {}) or {}
                merged = {**persisted_spotify_cfg, **base_spotify_cfg}
                legacy_redirects = {
                    "http://localhost:8080/api/channels/com.spotify.status/callback",
                    "http://localhost:5000/api/channels/com.spotify.status/callback",
                }
                canonical_redirect = "http://127.0.0.1:5000/api/channels/com.spotify.status/callback"
                if merged.get("redirect_uri") in legacy_redirects or merged.get("redirect_uri") is None:
                    merged["redirect_uri"] = canonical_redirect
                self.config["spotify"] = merged
            else:
                self._save_settings(self.config)
        except Exception as merge_e:  # noqa: BLE001
            logger.warning("[SpotifyStatusChannel] Failed merging persisted settings: %s", merge_e)
        
        # Initialize Spotify client
        if not self.degraded:
            self._initialize_spotify_client()
            # Start push manager early (it will block until authorized track calls succeed)
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
                    # Reuse already imported SpotifyService (avoid relative import that
                    # fails when module lacks package context during dynamic loading).
                    try:
                        self.spotify_service = SpotifyService(self.spotify_client, cache_ttl=self.cache_duration)
                    except Exception as svc_e:  # noqa: BLE001
                        logger.error("[SpotifyStatusChannel] Failed initializing SpotifyService: %s", svc_e)
                        self.spotify_service = None
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

            # If we become authorized later, push manager will start when first listener registered
            
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
                    # Only metadata (track/artist/album) changes trigger events now.
                    "push_event_types": ["now_playing_changed"],
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
                    "active": bool(self._push_manager and self._push_manager.thread_alive()),
                    "listener_count": self._push_manager.listener_count() if self._push_manager else 0,
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
        pm = self._push_manager
        return {
            "degraded": self.degraded,
            "authorized": self.spotify_client is not None,
            "cache_age_sec": (datetime.now() - self.cache_timestamp).total_seconds() if self.cache_timestamp else None,
            "listeners": pm.listener_count() if pm else 0,
            "push_active": bool(pm and pm.thread_alive()),
            "poll_interval": self.push_poll_interval,
        }
    
    async def request_image(self, request_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate current track album art image.

        Supports two rendering pipelines:
        - pillow (default) : existing raster implementation
        - svg (experimental): Jinja2 + SVG templates rasterized via CairoSVG (falls back to pillow if unavailable)
        """
        steps: list[str] = []
        reason: Optional[str] = None
        try:
            steps.append("parse_options")
            options = request_data.get("options", {}) if request_data else {}
            width = int(options.get("width", 800) or 800)
            height = int(options.get("height", 480) or 480)
            grayscale_flag = bool(options.get("grayscale", False))
            render_mode = str(options.get("render_mode", "pillow")).lower()

            include_base64_flag = bool((request_data or {}).get("include_base64", False))
            suppress_legacy = bool((request_data or {}).get("suppress_legacy_base64", not include_base64_flag))

            spotify_cfg = self.config.get("spotify", {})
            if not (spotify_cfg.get("client_id") and spotify_cfg.get("client_secret")):
                reason = "not_configured"
                return {"success": False, "error": "Spotify credentials not configured", "reason": reason, "steps": steps}
            if not self.spotify_client:
                reason = "not_authorized"
                return {"success": False, "error": "Spotify not authorized. Complete OAuth flow.", "reason": reason, "steps": steps}

            steps.append("get_current_track")
            track_info = self.get_current_track()

            image = None
            if render_mode == "svg" and self._svg_renderer.available:
                steps.append("svg_attempt")
                image = self._svg_renderer.render_image(track_info, width, height, theme=str(options.get("theme", "dark")))
                if image is None:
                    steps.append("svg_failed_fallback_pillow")
            if image is None:
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
            out_format = 'jpeg'
            content_type = 'image/jpeg'
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
                "grayscale": grayscale_flag,
                "render_mode": ("svg" if render_mode == "svg" and self._svg_renderer.available else "pillow"),
            }
            if image_base64 is not None:
                result["image"] = image_base64
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[SpotifyStatusChannel] Failed to generate image at step %s: %s", steps[-1] if steps else 'start', e)
            reason = reason or "generation_error"
            return {"success": False, "error": str(e) or "unknown", "reason": reason, "message": "Failed to generate Spotify status image", "steps": steps, "exception_type": type(e).__name__}
    
    # (HTML rendering helpers removed)

    # ...existing code...
    def get_router(self) -> APIRouter:
        """Delegate to external routes factory for cleaner separation."""
        # Load routes/main.py explicitly by file path to avoid relative import
        # issues when the plugin is loaded under a synthetic module name.
        try:
            routes_mod = _import_local("routes/main", file_name="routes/main.py")
            build_router = getattr(routes_mod, "build_router")
            logger.debug("[SpotifyStatusChannel] build_router loaded via explicit path")
            return build_router(self)
        except Exception as e:  # noqa: BLE001
            logger.error("[SpotifyStatusChannel] Failed to construct router: %s", e)
            raise

    # =========================================================================
    # Push / Event Streaming (delegated to PushManager)
    # =========================================================================
    def _ensure_push_manager(self):
        if self._push_manager is None:
            self._push_manager = PushManager(
                poll_interval=self.push_poll_interval,
                get_current_track=lambda: self.get_current_track(),
                webhook_url_getter=lambda: self.webhook_url,
            )

    def register_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._ensure_push_manager()
        assert self._push_manager is not None
        self._push_manager.add_listener(callback)

    def unregister_listener(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        if self._push_manager:
            self._push_manager.remove_listener(callback)

    def stop(self):  # Optional public stop hook
        if self._push_manager:
            self._push_manager.stop()


# Export the channel class for embedded plugin discovery
ChannelClass = SpotifyStatusChannel
