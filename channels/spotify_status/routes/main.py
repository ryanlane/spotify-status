"""FastAPI route construction for Spotify Status Channel.

Moved from top-level routes.py to routes/main.py for clearer structure.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles


class ChannelProtocol:  # pragma: no cover - structural typing hint
    config: Dict[str, Any]
    spotify_client: Any
    token_info: Optional[Dict[str, Any]]
    ui_dir: Any
    webhook_url: Optional[str]
    push_poll_interval: int
    degraded: bool
    supports_push: bool
    def get_manifest(self) -> Dict[str, Any]: ...  # noqa: D401,E701
    def get_status(self) -> Dict[str, Any]: ...
    def get_current_track(self) -> Optional[Dict[str, Any]]: ...
    async def request_image(self, request_data: Dict[str, Any] | None) -> Dict[str, Any]: ...
    def register_listener(self, cb): ...
    def unregister_listener(self, cb): ...
    def _initialize_spotify_client(self): ...
    def _save_settings(self, settings: Dict[str, Any]): ...
    def stop(self): ...


def build_router(channel: ChannelProtocol) -> APIRouter:
    # (Content identical to previous routes.py implementation)
    router = APIRouter()

    @router.get("/settings")
    async def get_settings():  # noqa: D401
        cfg = channel.config.get("spotify", {})
        masked = {
            "client_id": cfg.get("client_id"),
            "client_secret": ("***" + cfg.get("client_secret", "")[-4:]) if cfg.get("client_secret") else None,
            "redirect_uri": cfg.get(
                "redirect_uri",
                "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
            ),
            "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
            "authorized": bool(channel.spotify_client),
            "market": cfg.get("market"),
            "additional_types": cfg.get("additional_types"),
            "render_mode": cfg.get("render_mode", "pillow"),
        }
        return JSONResponse({"success": True, "settings": masked})

    @router.post("/settings")
    async def update_settings(payload: Dict[str, Any]):  # noqa: D401
        spotify_cfg = channel.config.setdefault("spotify", {})
        updated = False
        for key in ("client_id", "client_secret", "redirect_uri", "market", "additional_types", "render_mode"):
            if key in payload:
                spotify_cfg[key] = payload[key]
                updated = True
        if updated:
            channel._save_settings(channel.config)
            if any(k in payload for k in ("client_id", "client_secret")):
                channel._initialize_spotify_client()
        return JSONResponse({"success": True, "updated": updated})

    @router.get("/authorize")
    async def authorize():  # noqa: D401
        from spotipy.oauth2 import SpotifyOAuth  # lazy
        cfg = channel.config.get("spotify", {})
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        redirect_uri = cfg.get(
            "redirect_uri",
            "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
        )
        if not client_id or not client_secret:
            raise HTTPException(status_code=400, detail="Spotify client_id and client_secret must be configured first")
        scope = "user-read-currently-playing user-read-playback-state"
        try:
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=scope,
                cache_path=str(channel.ui_dir.parent / "data" / ".spotify_cache"),
                show_dialog=True,
            )
            auth_url = auth_manager.get_authorize_url()
            return JSONResponse({"success": True, "authorize_url": auth_url})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to build authorize URL: {e}")

    @router.get("/current-track")
    async def current_track():  # noqa: D401
        try:
            track_info = channel.get_current_track()
            if track_info:
                return JSONResponse({"success": True, "track": track_info})
            return JSONResponse({"success": False, "message": "No track currently playing"})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Failed to get current track: {e}")

    async def _handle_auth_callback(request: Request):
        if channel.degraded:
            raise HTTPException(status_code=500, detail="Spotipy dependency missing; cannot authorize")
        params = dict(request.query_params)
        code = params.get("code")
        error = params.get("error")
        if error:
            raise HTTPException(status_code=400, detail=f"Spotify authorization error: {error}")
        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code")
        cfg = channel.config.get("spotify", {})
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        redirect_uri = cfg.get(
            "redirect_uri",
            "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
        )
        redirect_fallbacks = [
            redirect_uri,
            "http://localhost:5000/api/channels/com.spotify.status/callback",
            "http://localhost:8080/api/channels/com.spotify.status/callback",
            "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
        ]
        seen = set()
        redirect_fallbacks = [r for r in redirect_fallbacks if not (r in seen or seen.add(r))]
        scope = "user-read-currently-playing user-read-playback-state"
        if not (client_id and client_secret):
            raise HTTPException(status_code=400, detail="Client credentials not configured")
        from spotipy.oauth2 import SpotifyOAuth  # local
        last_error: Optional[str] = None
        for attempt_idx, attempted_redirect in enumerate(redirect_fallbacks, start=1):
            try:
                auth_manager = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=attempted_redirect,
                    scope=scope,
                    cache_path=str(channel.ui_dir.parent / "data" / ".spotify_cache")
                )
                try:
                    token_info = auth_manager.get_access_token(code)  # type: ignore[arg-type]
                except TypeError:
                    token_info = auth_manager.get_access_token(code=code)  # type: ignore[call-arg]
                if token_info and token_info.get('access_token'):
                    if attempted_redirect != cfg.get("redirect_uri"):
                        cfg["redirect_uri"] = attempted_redirect
                        channel._save_settings(channel.config)
                    channel.token_info = token_info  # type: ignore[attr-defined]
                    channel.spotify_client = __import__('spotipy').Spotify(auth_manager=auth_manager)  # type: ignore[attr-defined]
                    # Immediately construct SpotifyService so first manifest/UI
                    # refresh reflects playback status without requiring a
                    # process restart or waiting for a lazy accessor.
                    try:  # noqa: BLE001
                        adaptive_cache_ttl = max(1, min(5, channel.push_poll_interval - 1))
                        from service import SpotifyService  # type: ignore
                        channel.spotify_service = SpotifyService(channel.spotify_client, cache_ttl=adaptive_cache_ttl)  # type: ignore[attr-defined]
                    except Exception:
                        # Non-fatal; lazy creation path in channel.get_current_track will recover.
                        pass
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
                continue
        raise HTTPException(status_code=500, detail=f"Auth callback failed after trying {len(redirect_fallbacks)} redirect(s): {last_error}")

    @router.get("/callback")
    async def callback_new(request: Request):  # noqa: D401
        return await _handle_auth_callback(request)

    @router.post("/callback")
    async def callback_new_post(request: Request):  # noqa: D401
        return await _handle_auth_callback(request)

    @router.get("/auth/callback")
    async def callback_legacy(request: Request):  # noqa: D401
        return await _handle_auth_callback(request)

    @router.post("/auth/callback")
    async def callback_legacy_post(request: Request):  # noqa: D401
        return await _handle_auth_callback(request)

    @router.get("/authorize/status")
    async def authorize_status():  # noqa: D401
        return JSONResponse({
            "authorized": channel.spotify_client is not None,
            "degraded": channel.degraded,
            "token_expires_at": (channel.token_info or {}).get('expires_at') if channel.token_info else None
        })

    @router.get("/authorize/redirects")
    async def authorize_redirects():  # noqa: D401
        cfg = channel.config.get("spotify", {})
        current = cfg.get("redirect_uri")
        candidates = [
            current,
            "http://127.0.0.1:5000/api/channels/com.spotify.status/callback",
            "http://localhost:5000/api/channels/com.spotify.status/callback",
            "http://localhost:8080/api/channels/com.spotify.status/callback",
        ]
        seen = set()
        ordered = [c for c in candidates if c and not (c in seen or seen.add(c))]
        return JSONResponse({
            "success": True,
            "current": current,
            "candidates": ordered,
            "note": "Order reflects fallback attempt priority in callback handler"
        })

    @router.get("/manifest")
    async def manifest_endpoint():  # noqa: D401
        return JSONResponse(channel.get_manifest())

    @router.post("/request_image")
    async def request_image_endpoint(payload: Dict[str, Any] | None = None):  # noqa: D401
        result = await channel.request_image(payload or {})
        return JSONResponse(result)

    @router.get("/health")
    async def health_endpoint():  # noqa: D401
        return JSONResponse({"success": True, "status": channel.get_status()})

    @router.get("/dependencies")
    async def dependencies_endpoint():  # noqa: D401
        return JSONResponse({
            "degraded": channel.degraded,
            "webhook_url": bool(channel.webhook_url),
            "poll_interval": channel.push_poll_interval,
        })

    def _test_payload() -> Dict[str, Any]:
        return {
            "success": True,
            "id": "com.spotify.status",
            "message": "Spotify channel responsive",
            "degraded": channel.degraded,
            "authorized": channel.spotify_client is not None
        }

    @router.post("/test")
    async def test_post():  # noqa: D401
        return JSONResponse(_test_payload())

    @router.get("/test")
    async def test_get():  # noqa: D401
        return JSONResponse(_test_payload())

    @router.post("/image_request")
    async def request_image_compat(payload: Dict[str, Any] | None = None):  # noqa: D401
        result = await channel.request_image(payload or {})
        result["compat_endpoint"] = True
        return JSONResponse(result)

    try:
        router.mount("/ui", StaticFiles(directory=str(channel.ui_dir)), name="ui")
    except Exception:  # noqa: BLE001
        pass

    @router.get("/ui/index.esm.js")
    async def ui_index_js():  # noqa: D401
        path = channel.ui_dir / "index.esm.js"
        if not path.exists():
            raise HTTPException(status_code=404, detail="index.esm.js not found")
        return FileResponse(str(path), media_type="text/javascript")

    @router.get("/ui/manage.esm.js")
    async def ui_manage_js():  # noqa: D401
        path = channel.ui_dir / "manage.esm.js"
        if not path.exists():
            raise HTTPException(status_code=404, detail="manage.esm.js not found")
        return FileResponse(str(path), media_type="text/javascript")

    @router.get("/ui/styles.css")
    async def ui_styles_css():  # noqa: D401
        path = channel.ui_dir / "styles.css"
        if not path.exists():
            raise HTTPException(status_code=404, detail="styles.css not found")
        return FileResponse(str(path), media_type="text/css")

    @router.get("/ui/index.html")
    async def ui_index_html():  # noqa: D401
        path = channel.ui_dir / "index.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(str(path), media_type="text/html")

    @router.get("/ui/debug-list")
    async def ui_debug_list():  # noqa: D401
        if not channel.ui_dir.exists():
            return JSONResponse({"exists": False, "path": str(channel.ui_dir)})
        files = []
        for p in sorted(channel.ui_dir.glob('*')):
            files.append({
                "name": p.name,
                "is_file": p.is_file(),
                "size": p.stat().st_size if p.is_file() else None,
                "mtime": p.stat().st_mtime
            })
        return JSONResponse({
            "exists": True,
            "path": str(channel.ui_dir),
            "count": len(files),
            "files": files
        })

    @router.get("/ui/ping")
    async def ui_ping():  # noqa: D401
        return JSONResponse({"ok": True, "message": "spotify ui router active"})

    @router.get("/push/status")
    async def push_status():  # noqa: D401
        pm = getattr(channel, "_push_manager", None)
        return JSONResponse({
            "supports_push": channel.supports_push,
            "thread_alive": bool(pm and pm.thread_alive()),
            "listeners": pm.listener_count() if pm else 0,
            "poll_interval": channel.push_poll_interval,
            "webhook": bool(channel.webhook_url)
        })

    @router.post("/push/trigger")
    async def push_trigger():  # noqa: D401
        pm = getattr(channel, "_push_manager", None)
        changed = pm.force_emit() if pm else False
        return JSONResponse({"forced": True, "emitted": changed})

    return router