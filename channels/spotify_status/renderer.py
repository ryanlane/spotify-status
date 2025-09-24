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
from typing import Dict, Any, Optional, Tuple, List
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
    # Multiplier applied to base font sizes (1.0 = current sizing)
    text_scale: float = 1.0
    # Layout mode: "landscape" (art left, text right), "portrait" (art top, text bottom),
    # "square" (two-column like landscape), or "auto" to infer from aspect ratio.
    layout: str = "auto"
    # Allow word-wrapping (instead of single-line ellipsis). Wrapped to max_lines below.
    wrap: bool = False
    # Maximum lines per text block when wrap=True (applies to artist/album/track separately)
    max_lines: int = 2


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
        # Base spacing scales with height; allow mild increase with text_scale as well
        margin = int(max(16, min(32, height * 0.04)) * max(0.9, min(1.4, options.text_scale)))
        gutter = int(max(16, min(40, height * 0.05)))

        # Decide layout
        layout = (options.layout or "auto").lower()
        if layout not in {"auto", "landscape", "portrait", "square"}:
            layout = "auto"
        if layout == "auto":
            ar = width / max(1, height)
            if ar >= 1.2:
                layout = "landscape"
            elif ar <= 0.83:
                layout = "portrait"
            else:
                layout = "square"

        # Canvas
        image = Image.new("RGB", (width, height), color="black")
        draw = ImageDraw.Draw(image)

        # Helpers to place album art and compute text region per layout
        def paste_album_art(x: int, y: int, size: int) -> Tuple[int, int, int, int]:
            """Paste album art (or draw placeholder) and return its bbox."""
            album_art = None
            if track_info.get("album_art_url"):
                album_art = self.download_album_art(track_info["album_art_url"])
            if album_art and size > 0:
                resample = None
                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
                elif hasattr(Image, "LANCZOS"):
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                fitted = ImageOps.fit(album_art.convert("RGB"), (size, size), method=resample)
                image.paste(fitted, (x, y))
            else:
                placeholder_bbox = [x, y, x + size, y + size]
                draw.rectangle(placeholder_bbox, fill="#CFCFCF")
                font_placeholder = self._get_font(max(14, int(height * 0.045)))
                self._center_in_rect(draw, "Album art here", font_placeholder, placeholder_bbox, fill="#111111")
            return (x, y, x + size, y + size)

        # Compute layout-specific regions
        if layout == "square":
            # Full-screen album art, no text
            album_art = None
            if track_info.get("album_art_url"):
                album_art = self.download_album_art(track_info["album_art_url"])
            if album_art:
                resample = None
                if hasattr(Image, "Resampling"):
                    resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
                elif hasattr(Image, "LANCZOS"):
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                fitted = ImageOps.fit(album_art.convert("RGB"), (width, height), method=resample)
                image.paste(fitted, (0, 0))
            else:
                placeholder_bbox = [0, 0, width, height]
                draw.rectangle(placeholder_bbox, fill="#CFCFCF")
                font_placeholder = self._get_font(max(14, int(height * 0.06)))
                self._center_in_rect(draw, "Album art here", font_placeholder, placeholder_bbox, fill="#111111")
            return image
        elif layout == "landscape":
            right_col_min = 300
            right_col_width = max(int(width * 0.36), right_col_min)
            right_col_right = width - margin
            right_col_left = right_col_right - right_col_width
            left_area_left = margin
            left_area_right = right_col_left - gutter
            left_area_width = max(0, left_area_right - left_area_left)
            art_size = max(0, min(left_area_width, height - 2 * margin))
            paste_album_art(left_area_left, margin, art_size)
            text_region = (right_col_left, margin, right_col_right, height - margin)
            text_align = "right"
        else:  # portrait
            # Art on top, centered; text flows below
            max_art_size = min(width - 2 * margin, int(height * 0.55))
            art_size = max(0, max_art_size)
            # Center horizontally
            art_x = margin + (width - 2 * margin - art_size) // 2
            paste_album_art(art_x, margin, art_size)
            text_region = (margin, margin + art_size + gutter, width - margin, height - margin)
            text_align = "left"

        # Text content
        artist_text = str(track_info.get("artist", "Unknown Artist"))
        album_text = str(track_info.get("album", "Unknown Album"))
        track_text = str(track_info.get("name", "Unknown Track"))

        # Fonts (scale with height and text_scale)
        base_artist = max(22, int(height * 0.12))
        base_album = max(16, int(height * 0.06))
        base_track = max(18, int(height * 0.08))
        sf = max(0.5, min(2.5, float(options.text_scale or 1.0)))
        artist_font = self._get_font(int(base_artist * sf))
        album_font = self._get_font(int(base_album * sf))
        track_font = self._get_font(int(base_track * sf))

        col_white = "#FFFFFF"
        col_gray = "#B0B0B0"
        line_gap = max(6, int(height * 0.015 * min(1.5, sf)))

        # Draw text within text_region
        tr_l, tr_t, tr_r, tr_b = text_region
        max_text_width = max(0, tr_r - tr_l)

        # Build line sets (either wrapped or fitted single-line)
        if options.wrap:
            artist_lines = self._wrap_text(draw, artist_text, artist_font, max_text_width, options.max_lines)
            album_lines = self._wrap_text(draw, album_text, album_font, max_text_width, options.max_lines)
            track_lines = self._wrap_text(draw, track_text, track_font, max_text_width, options.max_lines)
            get_block_h = lambda lines, font: self._multiline_height(draw, lines, font, line_gap)
            draw_block = self._draw_multiline_right_aligned if text_align == "right" else self._draw_multiline_left_aligned
            # Bottom stack: track, album, artist
            y = tr_b
            # Track
            h_block = get_block_h(track_lines, track_font)
            y -= h_block
            draw_block(draw, track_lines, track_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_white)
            y -= line_gap
            # Album
            h_block = get_block_h(album_lines, album_font)
            y -= h_block
            draw_block(draw, album_lines, album_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_gray)
            y -= line_gap
            # Artist
            h_block = get_block_h(artist_lines, artist_font)
            y -= h_block
            draw_block(draw, artist_lines, artist_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_white)
        else:
            # Single-line with ellipsis
            artist_fit = self._fit_text(draw, artist_text, artist_font, max_text_width)
            album_fit = self._fit_text(draw, album_text, album_font, max_text_width)
            track_fit = self._fit_text(draw, track_text, track_font, max_text_width)
            y = tr_b
            # Track
            track_h = self._text_height(draw, track_fit, track_font)
            y -= track_h
            if text_align == "right":
                self._draw_right_aligned(draw, track_fit, track_font, tr_r, y, fill=col_white)
            else:
                draw.text((tr_l, y), track_fit, font=track_font, fill=col_white)
            y -= line_gap
            # Album
            album_h = self._text_height(draw, album_fit, album_font)
            y -= album_h
            if text_align == "right":
                self._draw_right_aligned(draw, album_fit, album_font, tr_r, y, fill=col_gray)
            else:
                draw.text((tr_l, y), album_fit, font=album_font, fill=col_gray)
            y -= line_gap
            # Artist
            artist_h = self._text_height(draw, artist_fit, artist_font)
            y -= artist_h
            if text_align == "right":
                self._draw_right_aligned(draw, artist_fit, artist_font, tr_r, y, fill=col_white)
            else:
                draw.text((tr_l, y), artist_fit, font=artist_font, fill=col_white)

        return image

    def create_no_music_image(self, options: RenderOptions) -> Image.Image:
        """Render the same layout without a track – shows placeholders.

        Honors the same layout/text_scale/wrap options as create_status_image.
        """
        width, height = options.width, options.height
        margin = int(max(16, min(32, height * 0.04)) * max(0.9, min(1.4, options.text_scale)))
        gutter = int(max(16, min(40, height * 0.05)))
        layout = (options.layout or "auto").lower()
        if layout not in {"auto", "landscape", "portrait", "square"}:
            layout = "auto"
        if layout == "auto":
            ar = width / max(1, height)
            if ar >= 1.2:
                layout = "landscape"
            elif ar <= 0.83:
                layout = "portrait"
            else:
                layout = "square"

        image = Image.new("RGB", (width, height), color="black")
        draw = ImageDraw.Draw(image)

        # Album art placeholder + text region
        if layout == "square":
            # Already returned above; this branch won't execute. Kept for clarity.
            pass
        elif layout == "landscape":
            right_col_min = 300
            right_col_width = max(int(width * 0.36), right_col_min)
            right_col_right = width - margin
            right_col_left = right_col_right - right_col_width
            left_area_left = margin
            left_area_right = right_col_left - gutter
            left_area_width = max(0, left_area_right - left_area_left)
            art_size = max(0, min(left_area_width, height - 2 * margin))
            placeholder_bbox = [left_area_left, margin, left_area_left + art_size, margin + art_size]
            draw.rectangle(placeholder_bbox, fill="#CFCFCF")
            font_placeholder = self._get_font(max(14, int(height * 0.045)))
            self._center_in_rect(draw, "Album art here", font_placeholder, placeholder_bbox, fill="#111111")
            text_region = (right_col_left, margin, right_col_right, height - margin)
            text_align = "right"
        else:  # portrait
            max_art_size = min(width - 2 * margin, int(height * 0.55))
            art_size = max(0, max_art_size)
            art_x = margin + (width - 2 * margin - art_size) // 2
            placeholder_bbox = [art_x, margin, art_x + art_size, margin + art_size]
            draw.rectangle(placeholder_bbox, fill="#CFCFCF")
            font_placeholder = self._get_font(max(14, int(height * 0.045)))
            self._center_in_rect(draw, "Album art here", font_placeholder, placeholder_bbox, fill="#111111")
            text_region = (margin, margin + art_size + gutter, width - margin, height - margin)
            text_align = "left"

        # Fonts
        base_artist = max(22, int(height * 0.12))
        base_album = max(16, int(height * 0.06))
        base_track = max(18, int(height * 0.08))
        sf = max(0.5, min(2.5, float(options.text_scale or 1.0)))
        artist_font = self._get_font(int(base_artist * sf))
        album_font = self._get_font(int(base_album * sf))
        track_font = self._get_font(int(base_track * sf))

        col_white = "#FFFFFF"
        col_gray = "#B0B0B0"
        line_gap = max(6, int(height * 0.015 * min(1.5, sf)))
        tr_l, tr_t, tr_r, tr_b = text_region
        max_text_width = max(0, tr_r - tr_l)

        # Placeholder text
        artist_text = "Artist Name"
        album_text = "Album Name"
        track_text = "Track Name"

        if layout == "square":
            # Should not reach here; text suppressed in square mode.
            return image
        if options.wrap:
            artist_lines = self._wrap_text(draw, artist_text, artist_font, max_text_width, options.max_lines)
            album_lines = self._wrap_text(draw, album_text, album_font, max_text_width, options.max_lines)
            track_lines = self._wrap_text(draw, track_text, track_font, max_text_width, options.max_lines)
            get_block_h = lambda lines, font: self._multiline_height(draw, lines, font, line_gap)
            draw_block = self._draw_multiline_right_aligned if text_align == "right" else self._draw_multiline_left_aligned
            y = tr_b
            h_block = get_block_h(track_lines, track_font)
            y -= h_block
            draw_block(draw, track_lines, track_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_white)
            y -= line_gap
            h_block = get_block_h(album_lines, album_font)
            y -= h_block
            draw_block(draw, album_lines, album_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_gray)
            y -= line_gap
            h_block = get_block_h(artist_lines, artist_font)
            y -= h_block
            draw_block(draw, artist_lines, artist_font, tr_r if text_align == "right" else tr_l, y, max_text_width, line_gap, fill=col_white)
        else:
            artist_fit = self._fit_text(draw, artist_text, artist_font, max_text_width)
            album_fit = self._fit_text(draw, album_text, album_font, max_text_width)
            track_fit = self._fit_text(draw, track_text, track_font, max_text_width)
            y = tr_b
            track_h = self._text_height(draw, track_fit, track_font)
            y -= track_h
            if text_align == "right":
                self._draw_right_aligned(draw, track_fit, track_font, tr_r, y, fill=col_white)
            else:
                draw.text((tr_l, y), track_fit, font=track_font, fill=col_white)
            y -= line_gap
            album_h = self._text_height(draw, album_fit, album_font)
            y -= album_h
            if text_align == "right":
                self._draw_right_aligned(draw, album_fit, album_font, tr_r, y, fill=col_gray)
            else:
                draw.text((tr_l, y), album_fit, font=album_font, fill=col_gray)
            y -= line_gap
            artist_h = self._text_height(draw, artist_fit, artist_font)
            y -= artist_h
            if text_align == "right":
                self._draw_right_aligned(draw, artist_fit, artist_font, tr_r, y, fill=col_white)
            else:
                draw.text((tr_l, y), artist_fit, font=artist_font, fill=col_white)

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

    # --- Word wrapping helpers ---
    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        max_lines: int,
    ) -> List[str]:
        """Wrap text into lines that fit within max_width using pixel measurement.

        If the text exceeds max_lines, the last line is ellipsized to fit.
        """
        if not text:
            return [""]
        words = text.split()
        lines: List[str] = []
        current: List[str] = []
        i = 0
        n = len(words)
        while i < n:
            w = words[i]
            candidate = (" ".join(current + [w])).strip()
            if int(draw.textlength(candidate, font=font)) <= max_width or not current:
                current.append(w)
                i += 1
            else:
                lines.append(" ".join(current))
                current = []
                if len(lines) >= max_lines - 1:
                    # Last line; fit the remaining words with ellipsis
                    rest = " ".join(words[i:])
                    lines.append(self._fit_text(draw, rest, font, max_width))
                    return lines[:max_lines]
        if current:
            lines.append(" ".join(current))
        # Truncate if too many lines
        if len(lines) > max_lines:
            kept = lines[: max_lines - 1]
            kept.append(self._fit_text(draw, lines[max_lines - 1] + " " + " ".join(lines[max_lines:]), font, max_width))
            return kept
        return lines

    def _multiline_height(
        self,
        draw: ImageDraw.ImageDraw,
        lines: List[str],
        font: ImageFont.ImageFont,
        line_gap: int,
    ) -> int:
        return sum(self._text_height(draw, ln, font) for ln in lines) + line_gap * (max(0, len(lines) - 1))

    def _draw_multiline_right_aligned(
        self,
        draw: ImageDraw.ImageDraw,
        lines: List[str],
        font: ImageFont.ImageFont,
        right_x: int,
        y: int,
        max_width: int,
        line_gap: int,
        *,
        fill: str = "#FFFFFF",
    ) -> None:
        for i, ln in enumerate(lines):
            try:
                draw.text((right_x, y), ln, font=font, fill=fill, anchor="rt")
            except (TypeError, ValueError):
                tw = int(draw.textlength(ln, font=font))
                draw.text((right_x - min(tw, max_width), y), ln, font=font, fill=fill)
            y += self._text_height(draw, ln, font) + (line_gap if i < len(lines) - 1 else 0)

    def _draw_multiline_left_aligned(
        self,
        draw: ImageDraw.ImageDraw,
        lines: List[str],
        font: ImageFont.ImageFont,
        left_x: int,
        y: int,
        max_width: int,
        line_gap: int,
        *,
        fill: str = "#FFFFFF",
    ) -> None:
        for i, ln in enumerate(lines):
            draw.text((left_x, y), ln, font=font, fill=fill)
            y += self._text_height(draw, ln, font) + (line_gap if i < len(lines) - 1 else 0)

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
