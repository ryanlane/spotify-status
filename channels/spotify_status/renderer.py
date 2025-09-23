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
from typing import Dict, Any, Optional, Tuple
import io
import logging

import requests  # type: ignore
from PIL import Image, ImageDraw, ImageFont, ImageOps  # type: ignore

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
            try:
                return Image.open(io.BytesIO(response.content))
            except OSError as e:  # Invalid image bytes
                logger.error("Invalid album art image bytes: %s", e)
                return None
        except requests.RequestException as e:
            logger.error("Failed to download album art: %s", e)
            return None

    # ---- Status / Fallback Images ----
    def create_status_image(self, track_info: Dict[str, Any], options: RenderOptions) -> Image.Image:
        """Render the now playing layout (black theme, left art, right text).

        Layout (approximate):
        |<-- margin -->[  square album art  ]<-- gutter -->[       right text column        ]<-- margin -->|
        Background is black. Right column is bottom-aligned and right-anchored with three lines:
        - Artist (large, white)
        - Album (smaller, gray)
        - Track (medium, white)
        Text is ellipsized to fit the column width.
        """

        width, height = options.width, options.height
        margin = int(max(16, min(32, height * 0.04)))  # scale with height, 16–32px
        gutter = int(max(16, min(40, height * 0.05)))
        right_col_min = 300
        right_col_width = max(int(width * 0.36), right_col_min)

        # Canvas
        image = Image.new("RGB", (width, height), color="black")
        draw = ImageDraw.Draw(image)

        # Compute regions
        right_col_right = width - margin
        right_col_left = right_col_right - right_col_width
        left_area_left = margin
        left_area_right = right_col_left - gutter
        left_area_width = max(0, left_area_right - left_area_left)
        art_size = max(0, min(left_area_width, height - 2 * margin))

        # Download and paste album art as a square (cover)
        album_art = None
        if track_info.get("album_art_url"):
            album_art = self.download_album_art(track_info["album_art_url"])
        if album_art and art_size > 0:
            # Crop to square center and resize (Pillow compatibility for Resampling)
            resample = None
            if hasattr(Image, "Resampling"):
                resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
            elif hasattr(Image, "LANCZOS"):
                resample = Image.LANCZOS  # type: ignore[attr-defined]
            album_art = ImageOps.fit(
                album_art.convert("RGB"), (art_size, art_size), method=resample
            )
            image.paste(album_art, (left_area_left, margin))
        else:
            # Placeholder block (light gray) with centered label
            placeholder_bbox = [left_area_left, margin, left_area_left + art_size, margin + art_size]
            draw.rectangle(placeholder_bbox, fill="#CFCFCF")
            ph_text = "Album art here"
            font_placeholder = self._get_font(max(14, int(height * 0.045)))
            self._center_in_rect(draw, ph_text, font_placeholder, placeholder_bbox, fill="#111111")

        # Text content and fonts (scale with height)
        artist_text = track_info.get("artist", "Unknown Artist")
        album_text = track_info.get("album", "Unknown Album")
        track_text = track_info.get("name", "Unknown Track")

        artist_font = self._get_font(max(22, int(height * 0.12)))
        album_font = self._get_font(max(16, int(height * 0.06)))
        track_font = self._get_font(max(18, int(height * 0.08)))

        # Colors
        col_white = "#FFFFFF"
        col_gray = "#B0B0B0"

        # Right-align and bottom-stack the three lines within the right column
        max_text_width = right_col_width
        line_gap = max(6, int(height * 0.015))

        # Heights calculated later from fitted text

        y = height - margin  # bottom anchor
        # Track (bottom)
        track_fit = self._fit_text(draw, track_text, track_font, max_text_width)
        track_h2 = self._text_height(draw, track_fit, track_font)
        y -= track_h2
        self._draw_right_aligned(draw, track_fit, track_font, right_col_right, y, fill=col_white)
        y -= line_gap
        # Album (gray)
        album_fit = self._fit_text(draw, album_text, album_font, max_text_width)
        album_h2 = self._text_height(draw, album_fit, album_font)
        y -= album_h2
        self._draw_right_aligned(draw, album_fit, album_font, right_col_right, y, fill=col_gray)
        y -= line_gap
        # Artist (largest)
        artist_fit = self._fit_text(draw, artist_text, artist_font, max_text_width)
        artist_h2 = self._text_height(draw, artist_fit, artist_font)
        y -= artist_h2
        self._draw_right_aligned(draw, artist_fit, artist_font, right_col_right, y, fill=col_white)

        return image

    def create_no_music_image(self, options: RenderOptions) -> Image.Image:
        """Render the same layout without a track – shows placeholders."""
        width, height = options.width, options.height
        margin = int(max(16, min(32, height * 0.04)))
        gutter = int(max(16, min(40, height * 0.05)))
        right_col_min = 300
        right_col_width = max(int(width * 0.36), right_col_min)

        image = Image.new("RGB", (width, height), color="black")
        draw = ImageDraw.Draw(image)

        right_col_right = width - margin
        right_col_left = right_col_right - right_col_width
        left_area_left = margin
        left_area_right = right_col_left - gutter
        left_area_width = max(0, left_area_right - left_area_left)
        art_size = max(0, min(left_area_width, height - 2 * margin))

        # Placeholder album art panel
        placeholder_bbox = [left_area_left, margin, left_area_left + art_size, margin + art_size]
        draw.rectangle(placeholder_bbox, fill="#CFCFCF")
        font_placeholder = self._get_font(max(14, int(height * 0.045)))
        self._center_in_rect(draw, "Album art here", font_placeholder, placeholder_bbox, fill="#111111")

        # Right column placeholders similar to mock
        artist_font = self._get_font(max(22, int(height * 0.12)))
        album_font = self._get_font(max(16, int(height * 0.06)))
        track_font = self._get_font(max(18, int(height * 0.08)))

        col_white = "#FFFFFF"
        col_gray = "#B0B0B0"
        line_gap = max(6, int(height * 0.015))

        y = height - margin
        track_text = "Track Name"
        track_h = self._text_height(draw, track_text, track_font)
        y -= track_h
        self._draw_right_aligned(draw, track_text, track_font, right_col_right, y, fill=col_white)
        y -= line_gap
        album_text = "Album Name"
        album_h = self._text_height(draw, album_text, album_font)
        y -= album_h
        self._draw_right_aligned(draw, album_text, album_font, right_col_right, y, fill=col_gray)
        y -= line_gap
        artist_text = "Artist Name"
        artist_h = self._text_height(draw, artist_text, artist_font)
        y -= artist_h
        self._draw_right_aligned(draw, artist_text, artist_font, right_col_right, y, fill=col_white)

        return image

    # ---- Helpers ----
    def _load_fonts(self):
        try:
            font_large = ImageFont.truetype("arial.ttf", 24)
            font_medium = ImageFont.truetype("arial.ttf", 18)
            font_small = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        return font_large, font_medium, font_small

    def _center_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, y: int, *, middle: bool=False, fill: str="black"):
        try:
            anchor = "mm" if middle else "mt"
            draw.text((width // 2, y), text, font=font, anchor=anchor, fill=fill)
        except (TypeError, ValueError):
            tw = draw.textlength(text, font=font)
            draw.text((width // 2 - int(tw / 2), y), text, font=font, fill=fill)

    # -- New helpers for the right-column layout --
    def _get_font(self, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()

    def _text_bbox(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int, int, int]:
        # textbbox returns (left, top, right, bottom) on modern Pillow
        if hasattr(draw, "textbbox"):
            return draw.textbbox((0, 0), text, font=font)  # type: ignore[attr-defined]
        # Fallback approximation
        w = int(draw.textlength(text, font=font))
        h = font.size if hasattr(font, "size") else 16
        return (0, 0, w, h)

    def _text_height(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        _l, t, _r, b = self._text_bbox(draw, text, font)
        return max(0, b - t)

    def _fit_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        """Ellipsize text to fit within max_width."""
        if int(draw.textlength(text, font=font)) <= max_width:
            return text
        if max_width <= 0:
            return ""
        ellipsis = "…"
        # Binary search could be used; linear is fine for short strings
        s = text
        while s and int(draw.textlength(s + ellipsis, font=font)) > max_width:
            s = s[:-1]
        return (s + ellipsis) if s else ""

    def _draw_right_aligned(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        right_x: int,
        y: int,
        *,
        fill: str = "#FFFFFF",
    ) -> None:
        try:
            draw.text((right_x, y), text, font=font, fill=fill, anchor="rt")
        except (TypeError, ValueError):
            tw = int(draw.textlength(text, font=font))
            draw.text((right_x - tw, y), text, font=font, fill=fill)

    def _center_in_rect(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        rect: Tuple[int, int, int, int],
        *,
        fill: str = "#000000",
    ) -> None:
        l, t, r, b = rect
        cx = l + (r - l) // 2
        cy = t + (b - t) // 2
        try:
            draw.text((cx, cy), text, font=font, fill=fill, anchor="mm")
        except (TypeError, ValueError):
            tw = int(draw.textlength(text, font=font))
            fh = self._text_height(draw, text, font)
            draw.text((cx - tw // 2, cy - fh // 2), text, font=font, fill=fill)
