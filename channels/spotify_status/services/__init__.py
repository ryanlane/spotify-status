"""
Spotify service for handling API interactions
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SpotifyService:
    """Service for Spotify API interactions"""
    
    def __init__(self, spotify_client):
        """Initialize with Spotify client"""
        self.spotify_client = spotify_client
        self.cache = {}
        self.cache_duration = 30  # seconds
    
    def get_current_playback(self) -> Optional[Dict[str, Any]]:
        """Get current playback information"""
        try:
            if not self.spotify_client:
                return None
            
            # Check cache
            cache_key = "current_playback"
            if self._is_cache_valid(cache_key):
                return self.cache[cache_key]["data"]
            
            # Fetch from API
            current = self.spotify_client.current_playback()
            
            # Cache result
            self.cache[cache_key] = {
                "data": current,
                "timestamp": datetime.now()
            }
            
            return current
            
        except Exception as e:
            logger.error(f"Failed to get current playback: {e}")
            return None
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cache entry is still valid"""
        if cache_key not in self.cache:
            return False
        
        cache_entry = self.cache[cache_key]
        age = (datetime.now() - cache_entry["timestamp"]).total_seconds()
        return age < self.cache_duration


class ImageService:
    """Service for image processing and generation"""
    
    @staticmethod
    def resize_image(image, target_width: int, target_height: int):
        """Resize image maintaining aspect ratio"""
        from PIL import Image
        
        # Calculate the scaling factor
        width_ratio = target_width / image.width
        height_ratio = target_height / image.height
        scale_factor = min(width_ratio, height_ratio)
        
        # Calculate new dimensions
        new_width = int(image.width * scale_factor)
        new_height = int(image.height * scale_factor)
        
        # Resize the image
        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Create a new image with the target dimensions and paste the resized image
        result = Image.new('RGB', (target_width, target_height), color='white')
        x_offset = (target_width - new_width) // 2
        y_offset = (target_height - new_height) // 2
        result.paste(resized, (x_offset, y_offset))
        
        return result
    
    @staticmethod
    def create_text_image(text: str, width: int, height: int, font_size: int = 24):
        """Create an image with text"""
        from PIL import Image, ImageDraw, ImageFont
        
        image = Image.new('RGB', (width, height), color='white')
        draw = ImageDraw.Draw(image)
        
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
        # Calculate text position (centered)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        
        draw.text((x, y), text, font=font, fill='black')
        return image
