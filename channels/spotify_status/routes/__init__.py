"""Routes package for Spotify Status Channel.

Exposes build_router for external imports.
"""
from .main import build_router  # re-export
__all__ = ["build_router"]
