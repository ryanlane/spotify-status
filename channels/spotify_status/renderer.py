"""Rendering utilities for Spotify Status Channel.

This module isolates Pillow-based image construction so the main channel class
focuses on orchestration, API, and push event logic.

Responsibilities:
- Download album art (with timeout and error handling)
- Render current track status image
- Render fallback image when no music is playing

Future extension points:
- Additional renderers (e.g. SVG, Jinja2 HTML) can implement the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional
import io
import logging

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


@dataclass
class RenderOptions:
    width: int = 800
    height: int = 480
    grayscale: bool = False


class PillowRenderer:
    """Pillow implementation of status image generation."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    # ---- Album Art ----
    def download_album_art(self, album_art_url: str) -> Optional[Image.Image]:
        try:
            response = requests.get(album_art_url, timeout=self.timeout)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content))
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to download album art: %s", e)
            return None

    # ---- Status / Fallback Images ----
    def create_status_image(self, track_info: Dict[str, Any], options: RenderOptions) -> Image.Image:
        width, height = options.width, options.height
        album_art = None
        if track_info.get("album_art_url"):
            album_art = self.download_album_art(track_info["album_art_url"])

        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        if album_art:
            art_size = min(width, height) - 100
            try:
                album_art = album_art.resize((art_size, art_size), Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
            except Exception:
                album_art = album_art.resize((art_size, art_size))
            art_x = (width - art_size) // 2
            art_y = 20
            image.paste(album_art, (art_x, art_y))
            text_y = art_y + art_size + 20
        else:
            text_y = 50
            draw.rectangle([width // 4, 50, 3 * width // 4, height // 2], fill="lightgray", outline="black")
            try:
                draw.text((width // 2, height // 4), "♪", anchor="mm", fill="black")
            except Exception:
                w_note = draw.textlength("♪")
                draw.text((width // 2 - int(w_note / 2), height // 4 - 8), "♪", fill="black")

        font_large, font_medium, font_small = self._load_fonts()

        # Track name
        track_name = track_info.get("name", "Unknown Track")
        if len(track_name) > 30:
            track_name = track_name[:27] + "..."
        self._center_text(draw, track_name, font_large, width, text_y)

        # Artist
        artist_name = track_info.get("artist", "Unknown Artist")
        if len(artist_name) > 40:
            artist_name = artist_name[:37] + "..."
        self._center_text(draw, f"by {artist_name}", font_medium, width, text_y + 35, fill="gray")

        # Album
        album_name = track_info.get("album", "Unknown Album")
        if len(album_name) > 40:
            album_name = album_name[:37] + "..."
        self._center_text(draw, f"from {album_name}", font_small, width, text_y + 65, fill="gray")

        # Progress bar
        if track_info.get("progress_ms") and track_info.get("duration_ms"):
            progress = track_info["progress_ms"] / track_info["duration_ms"]
            bar_width = width - 100
            bar_height = 8
            bar_x = 50
            bar_y = text_y + 100
            draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height], fill="lightgray", outline="gray")
            progress_width = int(bar_width * progress)
            draw.rectangle([bar_x, bar_y, bar_x + progress_width, bar_y + bar_height], fill="black")
            current_time = f"{track_info['progress_ms'] // 60000}:{(track_info['progress_ms'] // 1000) % 60:02d}"
            total_time = f"{track_info['duration_ms'] // 60000}:{(track_info['duration_ms'] // 1000) % 60:02d}"
            draw.text((bar_x, bar_y + bar_height + 5), current_time, font=font_small, fill="gray")
            try:
                draw.text((bar_x + bar_width, bar_y + bar_height + 5), total_time, font=font_small, anchor="rt", fill="gray")
            except Exception:
                tw = draw.textlength(total_time, font=font_small)
                draw.text((bar_x + bar_width - tw, bar_y + bar_height + 5), total_time, font=font_small, fill="gray")

        device = track_info.get("device", "Unknown Device")	
        self._center_text(draw, f"Playing on {device}", font_small, width, height - 30, fill="gray")
        return image

    def create_no_music_image(self, options: RenderOptions) -> Image.Image:
        width, height = options.width, options.height
        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)
        try:
            font_large = ImageFont.truetype("arial.ttf", 36)
            font_medium = ImageFont.truetype("arial.ttf", 24)
        except Exception:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
        self._center_text(draw, "♪", font_large, width, height // 2 - 50, middle=True, fill="lightgray")
        self._center_text(draw, "No music playing", font_medium, width, height // 2 + 20, middle=True, fill="gray")
        self._center_text(draw, "Start playing on Spotify", font_medium, width, height // 2 + 50, middle=True, fill="lightgray")
        return image

    # ---- Helpers ----
    def _load_fonts(self):
        try:
            font_large = ImageFont.truetype("arial.ttf", 24)
            font_medium = ImageFont.truetype("arial.ttf", 18)
            font_small = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        return font_large, font_medium, font_small

    def _center_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, y: int, *, middle: bool=False, fill: str="black"):
        try:
            anchor = "mm" if middle else "mt"
            draw.text((width // 2, y), text, font=font, anchor=anchor, fill=fill)
        except Exception:
            tw = draw.textlength(text, font=font)
            draw.text((width // 2 - int(tw / 2), y), text, font=font, fill=fill)
