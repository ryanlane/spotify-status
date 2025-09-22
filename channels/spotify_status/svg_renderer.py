"""SVG template based renderer for Spotify status.

Renders Jinja2 SVG templates (portrait / landscape / square) and rasterizes
via CairoSVG (if available) into a Pillow Image so the rest of the pipeline
(grayscale, encoding) remains identical to the Pillow renderer path.

If CairoSVG or Jinja2 are not installed, this renderer reports unavailable and
callers should gracefully fall back to the Pillow renderer.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, Optional

try:  # Optional dependencies
    from jinja2 import Environment, FileSystemLoader, select_autoescape  # type: ignore
    _JINJA2_OK = True
except Exception:  # noqa: BLE001
    _JINJA2_OK = False

try:
    import cairosvg  # type: ignore
    _CAIRO_OK = True
except Exception:  # noqa: BLE001
    _CAIRO_OK = False

from PIL import Image  # type: ignore


class SvgRenderer:
    """Render track status using SVG templates.

    Usage:
        renderer = SvgRenderer(svg_dir)
        if renderer.available:
            img = renderer.render_image(track_dict, 800, 480, theme="dark")
    """

    def __init__(self, svg_dir: Path):
        self.svg_dir = svg_dir
        self.available = bool(_JINJA2_OK and _CAIRO_OK and svg_dir.exists())
        self._env: Optional[Environment] = None
        if self.available:
            try:
                self._env = Environment(
                    loader=FileSystemLoader(str(svg_dir)),
                    autoescape=select_autoescape(["svg"]),
                    enable_async=False,
                )
            except Exception:  # noqa: BLE001
                self.available = False

    def _select_template(self, width: int, height: int) -> str:
        # Aspect ratio heuristic
        if width >= height * 1.2:
            return "now_playing_landscape.svg.j2"
        if height >= width * 1.2:
            return "now_playing_portrait.svg.j2"
        return "now_playing_square.svg.j2"

    def _build_context(self, track: Optional[Dict[str, Any]], width: int, height: int, theme: str) -> Dict[str, Any]:
        if not track:
            return {
                "has_track": False,
                "width": width,
                "height": height,
                "theme": theme,
            }
        duration = track.get("duration_ms") or 0
        progress = track.get("progress_ms") or 0
        pct = (progress / duration * 100) if duration else 0
        return {
            "has_track": True,
            "width": width,
            "height": height,
            "theme": theme,
            "track_name": track.get("name"),
            "artist_name": track.get("artist"),
            "album_name": track.get("album"),
            "album_art_url": track.get("album_art_url"),
            "is_playing": track.get("is_playing"),
            "progress_ms": progress,
            "duration_ms": duration,
            "progress_pct": pct,
            "device": track.get("device"),
        }

    def render_image(self, track: Optional[Dict[str, Any]], width: int, height: int, theme: str = "dark") -> Optional[Image.Image]:
        if not self.available or not self._env:
            return None
        template_name = self._select_template(width, height)
        if not (self.svg_dir / template_name).exists():
            return None
        ctx = self._build_context(track, width, height, theme)
        try:
            svg = self._env.get_template(template_name).render(**ctx)
            png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width, output_height=height)
            return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None
