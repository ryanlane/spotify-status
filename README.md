# Spotify Now Playing — Mimir Source Plugin

A Spotify integration plugin for the [Mimir](https://github.com/ryanlane/mimir) platform. Displays the currently playing track as a generated image — album art, track metadata, or a top-items view — suitable for e-ink and other low-refresh displays.

**Plugin ID:** `com.spotify.status`
**Version:** 1.0.1
**Author:** Ryan Lane

---

## Features

- Displays currently playing Spotify track with album art
- Three visual modes: Album Art only, Album + Artist + Title overlay, Top Items
- Portrait and landscape layout support
- OAuth 2.0 authentication flow handled in-browser through the management UI
- Configurable update interval
- Fallback image when nothing is playing
- Dashboard card widget and full management page

---

## Prerequisites

Before installing, you need a Spotify Developer application with the correct redirect URI configured.

### Create a Spotify App

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new application
3. Under **Redirect URIs**, add:
   ```
   http://127.0.0.1:5000/api/channels/com.spotify.status/callback
   ```
   Adjust the host/port if your Mimir API runs elsewhere.
4. Note your **Client ID** and **Client Secret**

---

## Installation

### Via Mimir Plugin Store (recommended)

Open the Mimir UI, go to **Sources**, click **Browse Store**, and search for "Spotify". Click **Install**.

After installing, open the source settings and enter your Client ID and Client Secret, then complete the OAuth flow from the management interface.

### Via git URL

In **Sources → Install Source**, paste:

```
https://github.com/ryanlane/mimir-channel-spotify.git
```

### Manual

```bash
git clone https://github.com/ryanlane/mimir-channel-spotify.git
cp -r mimir-channel-spotify/channels/spotify_status /path/to/mimir-api/channels/
pip install -r channels/spotify_status/requirements.txt
```

Restart (or hot-reload) the Mimir API — the channel is auto-discovered.

---

## Requirements

- Mimir Platform v2.1.0+
- Python 3.8+
- `fastapi`, `requests`, `pillow`, `spotipy`
- A Spotify account (free or premium)

---

## Configuration

Settings are entered through the plugin's management interface or at `/api/channels/com.spotify.status/settings`.

| Setting | Type | Default | Description |
|---|---|---|---|
| `client_id` | string | — | Spotify app Client ID |
| `client_secret` | string | — | Spotify app Client Secret |
| `redirect_uri` | string | `http://127.0.0.1:5000/api/channels/com.spotify.status/callback` | Must match exactly what's in your Spotify app |
| `visual` | string | `"Album Art"` | Display mode: `Album Art`, `Album, Artist, and Title`, `Top Items` |
| `orientation` | string | — | Layout: `portrait` or `landscape` |
| `update_interval_value` | integer | `30` | How often Mimir polls this channel for a new image |
| `update_interval_unit` | string | `"minutes"` | Unit for interval: `seconds`, `minutes`, `hours`, `days` |

### First-time authentication

After saving your Client ID and Client Secret, open the management interface and follow the "Connect Spotify" link. You'll be redirected to Spotify to authorize the `user-read-currently-playing` scope. On success you'll be redirected back and the channel will be ready.

The OAuth token is cached in `data/.spotify_cache` and refreshed automatically.

---

## API Endpoints

All endpoints are prefixed with `/api/channels/com.spotify.status`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/manifest` | Channel capabilities and schema |
| `POST` | `/request_image` | Generate and return the current track image |
| `GET` | `/current-track` | Get currently playing track metadata as JSON |
| `GET` | `/health` | Check Spotify API connectivity and auth status |
| `GET` | `/callback` | OAuth redirect handler (Spotify → Mimir) |

---

## Management Interface

The plugin registers a management page accessible by clicking the source in **Sources**. From there you can:

- Enter and save Spotify credentials
- Authorize the Spotify connection
- View the currently playing track in real time
- Manually trigger an image refresh
- Switch visual modes and orientation

A compact dashboard card is also available showing live playback status.

---

## File Structure

```
channels/spotify_status/
├── plugin.json          # Channel manifest (id, schema, UI registration)
├── channel.py           # SpotifyStatusChannel implementation
├── services/            # Spotify API client and image generation
├── models/              # Data models
├── requirements.txt     # Python dependencies
├── ui/
│   ├── index.esm.js     # Dashboard card Web Component
│   ├── manage.esm.js    # Management page Web Component
│   └── styles.css       # Component styles
└── data/
    └── .spotify_cache   # Spotipy OAuth token cache (auto-managed)
```

---

## Troubleshooting

**Authentication error / redirect URI mismatch:** The URI in your Spotify Developer Dashboard must match `redirect_uri` in your settings exactly — including protocol, host, port, and path. No trailing slashes.

**Nothing playing / fallback image shown:** The plugin shows a fallback when Spotify reports no active playback. Start playing something on any Spotify client connected to your account.

**Token expired:** Spotipy handles token refresh automatically. If refresh fails, clear `data/.spotify_cache` and re-authorize from the management interface.

**Wrong account:** The Spotify account used to authorize must be the same one currently playing music. Authorizing with a different account will not see playback from the other.

**Health check:**
```bash
curl http://localhost:5000/api/channels/com.spotify.status/health
```

---

## License

Same terms as the Mimir platform.
