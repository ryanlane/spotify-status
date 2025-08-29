# Spotify Status Channel Plugin

A Mimir platform plugin that displays currently playing Spotify track information and album art for e-paper displays.

## Features

- 🎵 Real-time Spotify playback monitoring
- 🖼️ Album art display with metadata overlay
- 📊 Progress bar and track information
- 🌐 Web UI for configuration and testing
- 🔄 Automatic refresh and caching
- 📱 Responsive design for e-paper displays

## Setup Instructions

### 1. Spotify App Configuration

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new application
3. Note your `Client ID` and `Client Secret`
4. Add `http://localhost:8080/callback` to the Redirect URIs
5. Save your settings

### 2. Plugin Installation

1. Copy this plugin directory to your Mimir channels folder:
   ```bash
   cp -r channels/spotify_status /path/to/mimir-api/channels/
   ```

2. Install required Python dependencies:
   ```bash
   pip install spotipy pillow requests pydantic
   ```

### 3. Configuration

Create a configuration file for the plugin with your Spotify credentials:

```json
{
  "spotify": {
    "client_id": "your_spotify_client_id",
    "client_secret": "your_spotify_client_secret",
    "redirect_uri": "http://localhost:8080/callback"
  },
  "display": {
    "default_width": 800,
    "default_height": 480,
    "include_progress": true,
    "include_metadata": true
  }
}
```

### 4. Authentication

1. Start the Mimir API server
2. Visit the plugin UI at: `http://localhost:8080/api/channels/com.spotify.status/ui/index.html`
3. The first time you access the API, you'll be redirected to Spotify for authentication
4. Grant the required permissions (read currently playing track)
5. You'll be redirected back and the plugin will be ready to use

## API Endpoints

### Core Plugin Endpoints

- `GET /api/channels/com.spotify.status/manifest` - Plugin capabilities and info
- `POST /api/channels/com.spotify.status/request_image` - Generate status image
- `GET /api/channels/com.spotify.status/health` - Check plugin health

### Custom Endpoints

- `GET /api/channels/com.spotify.status/current-track` - Get current track info
- `POST /api/channels/com.spotify.status/auth/callback` - OAuth callback handler

## Usage Examples

### Get Current Track
```bash
curl http://localhost:8080/api/channels/com.spotify.status/current-track
```

### Generate Status Image
```bash
curl -X POST http://localhost:8080/api/channels/com.spotify.status/request_image \
  -H "Content-Type: application/json" \
  -d '{"options": {"width": 800, "height": 480}}'
```

## Image Generation

The plugin generates images with:

- **Album Art**: Downloaded from Spotify and resized to fit
- **Track Information**: Song title, artist, and album name
- **Progress Bar**: Current playback position
- **Device Info**: What device is playing the music
- **Fallback**: "No music playing" message when nothing is active

### Customization Options

- `width` / `height`: Display dimensions (default: 800x480)
- `include_metadata`: Show track info text (default: true)
- `include_progress`: Show progress bar (default: true)

## Web UI

Access the management interface at:
```
http://localhost:8080/api/channels/com.spotify.status/ui/index.html
```

Features:
- Real-time track status
- Connection status indicator
- Manual refresh and image generation
- Error handling and diagnostics

## File Structure

```
channels/spotify_status/
├── plugin.json              # Plugin configuration
├── channel.py               # Main plugin class
├── services/
│   └── __init__.py         # Spotify and image services
├── models/
│   └── __init__.py         # Data models
├── ui/
│   ├── index.html          # Web interface
│   ├── manage.esm.js       # Web component
│   └── styles.css          # Styling
└── data/                   # Runtime data (OAuth cache, etc.)
```

## Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Check your Spotify app credentials
   - Ensure redirect URI matches exactly
   - Clear the OAuth cache in `data/.spotify_cache`

2. **No Music Detected**
   - Start playing music on Spotify
   - Check that the same Spotify account is used for auth
   - Verify the required scopes are granted

3. **Image Generation Fails**
   - Check internet connectivity for album art download
   - Verify PIL/Pillow is installed correctly
   - Check logs for specific error messages

### Debugging

Enable debug logging by setting the log level:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## License

This plugin is part of the Mimir platform and follows the same licensing terms.
