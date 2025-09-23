# Spotify Status Channel

Display the currently playing Spotify track (album art + metadata) on a Mimir display.

## Features
- OAuth-based connection to a Spotify account
- Album art rendering with track, artist, album, progress bar, and device name
- Fallback image when nothing is playing
- Configurable: client credentials, redirect URI, market, additional item types
- Simple REST endpoints for settings + authorization flow

## Endpoints (Channel Scope)
Base path (example): `/api/channels/com.spotify.status`

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/manifest` | Channel manifest & status summary |
| GET | `/settings` | Current (masked) Spotify settings & auth state |
| POST | `/settings` | Update credentials / market / additional_types |
| GET | `/authorize` | Get Spotify authorize URL (begin OAuth) |
| POST | `/auth/callback?code=...` | OAuth redirect handler (code exchange) |
| GET | `/current-track` | Raw currently playing track metadata |

The platform will also call `request_image` internally to obtain the rendered album art image payload.

## Configuration Flow
1. Create a Spotify application at https://developer.spotify.com/dashboard
2. Add the Redirect URI you plan to use (e.g. `http://localhost:8080/callback`) in the Spotify app settings
3. Call `POST /settings` with:
```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "redirect_uri": "http://localhost:8080/callback",
  "market": "US",                  // optional (ISO 3166-1 alpha-2)
  "additional_types": "track,episode" // optional, defaults to just track
}
```
4. Call `GET /authorize` -> open the returned `authorize_url` in a browser.
5. Log in & accept scopes. Spotify redirects to your redirect URI with `?code=...`.
6. POST `/auth/callback?code=...` to finalize. Manifest/settings should now show `authorized: true`.
7. The next platform image request should return the album art (or no-music placeholder).

## Settings Fields
| Field | Description | Example |
| ----- | ----------- | ------- |
| client_id | Spotify application client ID | `abcd1234` |
| client_secret | Spotify application client secret | (masked in GET) |
| redirect_uri | Must match one configured in Spotify dashboard | `http://localhost:8080/callback` |
| market | Optional market filter (country code) | `US` |
| additional_types | Comma-separated: `track`, `episode` | `track,episode` |

`additional_types` is passed to `GET /me/player/currently-playing` so podcasts (episodes) can appear if desired.

## Image Request Behavior
`request_image` returns structured error objects until fully configured:
- Not configured (no credentials): `{ "success": false, "error": "not_configured" }`
- Not authorized (OAuth not completed): `{ "success": false, "error": "not_authorized" }`
- Success (music playing): Base64 JPEG with track metadata
- Success (idle): Base64 JPEG placeholder saying no music playing

## Rendering Notes
- Default size 800x480 (override via platform request options `width` / `height` if supported)
- Album art scaled with Lanczos filter
- Progress bar shows elapsed vs total track time
- Artist / album names truncated to avoid overflow

## Troubleshooting
| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `not_configured` | Missing `client_id` or `client_secret` | POST /settings with credentials |
| `not_authorized` | OAuth flow not finished / token expired | Re-run `/authorize` and callback |
| No album art | Track has no images array | Fallback placeholder area drawn |
| Always “No music playing” | Playback on different account/device | Ensure the same account authorized; start playback in Spotify app |
| 401 errors in logs | Token expired / revoked | Re-authorize via `/authorize` |

## Security / Privacy
- Secrets are never returned verbatim (only a masked last 4 chars of `client_secret`)
- Token cache stored at `data/.spotify_cache` (Spotipy format). Protect filesystem perms.
- Consider rotating client secret periodically in Spotify dashboard.

## Future Improvements (Optional)
- Add state parameter validation for OAuth anti-CSRF
- UI management panel (web components) for entering credentials
- Token refresh telemetry & expiration countdown in manifest
- Custom themes for image rendering (dark/light)

## License
Assumes same license as the containing repository unless overridden.

## SVG Template Conversion (Figma -> Jinja2)
A helper script `scripts/figma_svg_to_template.py` converts raw exported Figma SVGs into Jinja2 templates compatible with the channel's `SvgRenderer`.

### Why
Design tools export verbose SVG with duplicated inline styles. The script:
- Consolidates repeated styles into a single `<style>` block with compact class names.
- Replaces text literals with dynamic Jinja placeholders (track, artist, etc.).
- Injects `{{ width }}` / `{{ height }}` placeholders for scalable rendering.
- (Optional) Applies theme-aware color substitutions (e.g. light vs dark).

### Basic Usage
```
python scripts/figma_svg_to_template.py input.svg -o svg/now_playing_new.svg.j2
```

### With Text & Theme Mapping
```
python scripts/figma_svg_to_template.py figma_now_playing.svg \
  -o svg/now_playing_portrait_auto.svg.j2 \
  --map "Track Title={{ track_name }}" "Artist Name={{ artist_name }}" \
        "Album Name={{ album_name }}" \
  --map "0:00 / 0:00={{ (progress_ms // 60000) }}:{{ '%02d' % ((progress_ms // 1000) % 60) }} / {{ (duration_ms // 60000) }}:{{ '%02d' % ((duration_ms // 1000) % 60) }}" \
  --theme-substitute "#FFFFFF={{ '#ffffff' if theme == 'light' else '#111418' }}" \
  --title "Auto Converted Portrait"
```

You can also provide a JSON mapping file (see `scripts/sample_text_mapping.json`):
```
python scripts/figma_svg_to_template.py figma_now_playing.svg \
  -o svg/now_playing_portrait_auto.svg.j2 \
  --map-json scripts/sample_text_mapping.json
```

### Color Theme Substitution
Pass one or more `--theme-substitute` rules of the form:
```
COLOR=JINJA_EXPR
```
Example:
```
--theme-substitute "#000000={{ '#111418' if theme == 'light' else '#f5f7fa' }}" \
                   "#FFFFFF={{ '#ffffff' if theme == 'light' else '#111418' }}"
```
If a class style contains that exact color value it will be replaced by the Jinja expression.

### Output
- The script writes a `.svg.j2` file starting with helper assignments: `{% set W = width %}{% set H = height %}`.
- A `<defs><style><![CDATA[ ... ]]></style></defs>` block contains consolidated classes `.c1`, `.c2`, etc.
- Original geometry and structure preserved (unsupported elements are left untouched).

### Tips
- Keep Figma export constrained to a single artboard for predictable dimensions.
- After conversion you can manually rename classes or embed additional conditional logic.
- Run through `SvgRenderer` by selecting render mode `svg` in requests.

### Limitations
- Does not auto-create multiple aspect ratio templates: run separately per design.
- Only a limited set of style attributes consolidated (extend `STYLE_ATTRS` in script as needed).
