// Spotify Status Manager Web Component for Mimir Platform v2.5
class SpotifyStatusManager extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    
    this.state = {
      currentTrack: null,
      isConnected: false,
      isLoading: true,
      error: null,
      // settings related
      configured: false,
      authorized: false,
      clientId: '',
      clientSecret: '',
      redirectUri: '',
      saving: false
    };

    this.apiBaseUrl = this.getApiBaseUrl();
  }

  getApiBaseUrl() {
    return window.mimirServerBaseUrl || window.location.origin;
  }

  async connectedCallback() {
    await this.loadSettings();
    if (this.state.configured) {
      await this.loadStatus();
      this.startAutoRefresh();
    }
    this.render();
  }

  async loadSettings() {
    try {
      const resp = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/settings`, { credentials: 'include' });
      const data = await resp.json();
      if (data.success) {
        const s = data.settings || {};
  // Canonical local redirect (force 127.0.0.1 to avoid host variance like 'oak' or 'localhost')
  const defaultRedirect = `http://127.0.0.1:5000/api/channels/com.spotify.status/callback`;
        this.setState({
          configured: !!s.configured,
          authorized: !!s.authorized,
          clientId: s.client_id || '',
          // secret masked; always blank for security on load
          redirectUri: s.redirect_uri || defaultRedirect
        });
      }
    } catch (e) {
      console.warn('Failed to load settings', e);
    }
  }

  async saveSettings() {
    try {
      this.setState({ saving: true, error: null });
      const body = {
        client_id: this.state.clientId.trim(),
        client_secret: this.state.clientSecret.trim(),
        redirect_uri: this.state.redirectUri.trim()
      };
      const resp = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (data.success) {
        await this.loadSettings();
        if (this.state.configured) {
          await this.loadStatus();
          this.startAutoRefresh();
        }
      } else {
        this.setState({ error: 'Failed to save settings' });
      }
    } catch (e) {
      this.setState({ error: 'Save failed: ' + e });
    } finally {
      this.setState({ saving: false });
    }
  }

  async loadStatus() {
    try {
      this.setState({ isLoading: true, error: null });
      
      const response = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/current-track`, {
        credentials: 'include'
      });
      
      const data = await response.json();
      
      if (data.success && data.track) {
        this.setState({
          currentTrack: data.track,
          isConnected: true,
          isLoading: false
        });
      } else {
        this.setState({
          currentTrack: null,
          isConnected: true,
          isLoading: false
        });
      }
    } catch (error) {
      console.error('Failed to load Spotify status:', error);
      this.setState({
        isConnected: false,
        isLoading: false,
        error: 'Failed to connect to Spotify API'
      });
    }
  }

  setState(updates) {
    Object.assign(this.state, updates);
    this.render();
  }

  render() {
    // First-run configuration form
    if (!this.state.configured) {
      this.shadowRoot.innerHTML = `
        <style>${this.formStyles()}</style>
        <div class="container">
          <div class="card">
            <div class="header">
              <h1>🎵 Spotify Status</h1>
              <p>Enter your Spotify API credentials to get started</p>
            </div>
            <form id="config-form">
              <label>
                <span>Client ID</span>
                <input type="text" id="client-id" value="${this.escapeHtml(this.state.clientId)}" placeholder="sp_client_id" required />
              </label>
              <label>
                <span>Client Secret</span>
                <input type="password" id="client-secret" value="" placeholder="sp_client_secret" ${this.state.clientId? '' : 'required'} />
              </label>
              <label>
                <span>Redirect URI</span>
                <input type="text" id="redirect-uri" value="${this.escapeHtml(this.state.redirectUri)}" />
              </label>
              <div class="actions">
                <button type="submit" class="btn" ${this.state.saving? 'disabled' : ''}>${this.state.saving? 'Saving...' : 'Save Settings'}</button>
              </div>
              ${this.state.error ? `<div class="error">${this.escapeHtml(this.state.error)}</div>` : ''}
              <div class="help">
                After saving, you'll authorize the channel with your Spotify account.
              </div>
            </form>
          </div>
        </div>
      `;
      this.attachConfigHandlers();
      return;
    }

    // Existing status UI once configured
    this.shadowRoot.innerHTML = `
      <style>
        :host {display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
        .container {max-width:600px;margin:0 auto;padding:24px;}
        .card {background:#fff;border-radius:12px;padding:32px;box-shadow:0 4px 12px rgba(0,0,0,0.1);} 
        .header {text-align:center;margin-bottom:32px;} 
        .header h1 {color:#1db954;margin:0 0 8px;} 
        .status {display:flex;align-items:center;margin-bottom:24px;padding:16px;background:#f8f9fa;border-radius:8px;} 
        .status-dot {width:12px;height:12px;border-radius:50%;margin-right:12px;} 
        .status-dot.connected {background:#1db954;} 
        .status-dot.disconnected {background:#dc3545;} 
        .track-info {background:${this.state.currentTrack ? '#f0fdf4' : '#f8f9fa'};border:1px solid ${this.state.currentTrack ? '#1db954' : '#dee2e6'};border-radius:8px;padding:20px;margin-bottom:24px;} 
        .track-title {font-size:1.2rem;font-weight:600;margin-bottom:8px;color:#212529;} 
        .track-artist {color:#6c757d;margin-bottom:4px;} 
        .track-album {color:#9ca3af;font-size:.9rem;margin-bottom:12px;} 
        .progress-bar {width:100%;height:4px;background:#e9ecef;border-radius:2px;margin:12px 0;overflow:hidden;} 
        .progress-fill {height:100%;background:#1db954;transition:width 1s linear;} 
        .time-info {display:flex;justify-content:space-between;font-size:.8rem;color:#6c757d;} 
        .controls {display:flex;gap:12px;justify-content:center;} 
        .btn {background:#1db954;color:#fff;border:none;border-radius:6px;padding:10px 20px;cursor:pointer;font-size:.9rem;transition:background .2s;} 
        .btn:hover {background:#1ed760;} 
        .btn-secondary {background:#6c757d;} .btn-secondary:hover {background:#5a6268;} 
        .loading {text-align:center;padding:40px;color:#6c757d;} 
        .error {background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:16px;border-radius:8px;margin-top:16px;} 
        .auth-warning {background:#fff3cd;border:1px solid #ffeeba;color:#856404;padding:12px;border-radius:8px;margin-bottom:16px;font-size:.85rem;} 
      </style>
      <div class="container">
        <div class="card">
          <div class="header">
            <h1>🎵 Spotify Status</h1>
            <p>Monitor your currently playing track</p>
          </div>
          ${!this.state.authorized ? `<div class="auth-warning">Credentials saved. Authorize with Spotify to enable live status.<br/><button id="authorize-btn" class="btn" style="margin-top:8px;">Authorize Spotify</button></div>` : ''}
          ${this.state.isLoading ? `<div class="loading"><h3>Loading Spotify status...</h3></div>` : `
            <div class="status">
              <div class="status-dot ${this.state.isConnected ? 'connected' : 'disconnected'}"></div>
              <span>${this.getStatusText()}</span>
            </div>
            <div class="track-info">${this.renderTrackInfo()}</div>
            <div class="controls">
              <button class="btn" id="refresh-btn">🔄 Refresh</button>
              <button class="btn" id="generate-btn">🖼️ Generate Image</button>
            </div>
            ${this.state.error ? `<div class="error">${this.state.error}</div>` : ''}
            ${!this.state.isConnected ? `<div class="error">Unable to connect to Spotify. Please check your settings and network connection.</div>` : ''}
            ${this.state.currentTrack ? '' : `<div class="error">No track is currently playing. Start a track in Spotify to see the status here.</div>`}
          `}
          ${!this.state.authorized && !this.state.isLoading ? `
            <div class="degraded-mode">
              <div class="error" style="margin-top: 16px;">
                ⚠️ Live status not available. Please authorize the app with Spotify.
              </div>
            </div>
          ` : ''}
        </div>
      </div>`;
    this.attachEventListeners();
  }

  formStyles() {
    return `:host{display:block;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;} .container{max-width:640px;margin:0 auto;padding:32px;} .card{background:#fff;border-radius:16px;padding:40px;box-shadow:0 4px 16px rgba(0,0,0,0.08);} h1{margin:0 0 8px;color:#1db954;} p{margin:0 0 28px;color:#495057;} form{display:flex;flex-direction:column;gap:20px;} label{display:flex;flex-direction:column;font-size:.8rem;gap:6px;font-weight:600;color:#343a40;} input{padding:10px 14px;border:1px solid #ced4da;border-radius:8px;font-size:.85rem;font-family:inherit;} input:focus{outline:none;border-color:#1db954;box-shadow:0 0 0 3px rgba(29,185,84,.25);} .actions{text-align:right;margin-top:8px;} .btn{background:#1db954;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:.85rem;cursor:pointer;font-weight:600;} .btn[disabled]{opacity:.6;cursor:not-allowed;} .btn:hover:not([disabled]){background:#1ed760;} .error{background:#f8d7da;border:1px solid #f5c6cb;color:#721c24;padding:12px 16px;border-radius:8px;font-size:.75rem;} .help{font-size:.7rem;color:#6c757d;margin-top:4px;}`;
  }

  attachConfigHandlers() {
    const form = this.shadowRoot.getElementById('config-form');
    if (!form) return;
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const clientIdEl = this.shadowRoot.getElementById('client-id');
      const clientSecretEl = this.shadowRoot.getElementById('client-secret');
      const redirectUriEl = this.shadowRoot.getElementById('redirect-uri');
      this.setState({
        clientId: clientIdEl.value,
        clientSecret: clientSecretEl.value,
        redirectUri: redirectUriEl.value
      });
      this.saveSettings();
    });
  }

  attachEventListeners() {
    const refreshBtn = this.shadowRoot.getElementById('refresh-btn');
    const generateBtn = this.shadowRoot.getElementById('generate-btn');
    const authorizeBtn = this.shadowRoot.getElementById('authorize-btn');

    if (refreshBtn) refreshBtn.addEventListener('click', () => this.loadStatus());
    if (generateBtn) generateBtn.addEventListener('click', () => this.generateImage());
    if (authorizeBtn) authorizeBtn.addEventListener('click', () => this.beginAuthorize());
  }

  async beginAuthorize() {
    try {
      const resp = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/authorize`, { credentials: 'include' });
      const data = await resp.json();
      if (data.success && data.authorize_url) {
        const authWin = window.open(data.authorize_url, '_blank');
        // Poll authorization status every 3s until authorized or window closed
        const poll = async () => {
          try {
            const stResp = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/authorize/status`, { credentials: 'include' });
            const stData = await stResp.json();
            if (stData.authorized) {
              this.setState({ authorized: true, error: null });
              await this.loadStatus();
              if (!this.refreshInterval) this.startAutoRefresh();
              if (authWin && !authWin.closed) authWin.close();
              return;
            }
            if (authWin && authWin.closed) return; // stop if user closed
            setTimeout(poll, 3000);
          } catch (e) {
            console.warn('Auth status poll failed', e);
            setTimeout(poll, 4000); // backoff
          }
        };
        setTimeout(poll, 2500);
      } else {
        this.setState({ error: 'Failed to start authorization flow' });
      }
    } catch (e) {
      this.setState({ error: 'Authorization start failed: ' + e });
    }
  }

  getStatusText() {
    if (!this.state.isConnected) {
      return 'Connection failed';
    }
    return this.state.currentTrack ? 'Connected to Spotify' : 'Connected but no music playing';
  }

  renderTrackInfo() {
    if (!this.state.currentTrack) {
      return `
        <div class="track-title">No music playing</div>
        <div class="track-artist">Start playing music on Spotify to see it here</div>
      `;
    }

    const track = this.state.currentTrack;
    const progressPercent = track.progress_ms && track.duration_ms 
      ? (track.progress_ms / track.duration_ms) * 100 
      : 0;

    return `
      <div class="track-title">${this.escapeHtml(track.name)}</div>
      <div class="track-artist">by ${this.escapeHtml(track.artist)}</div>
      <div class="track-album">from ${this.escapeHtml(track.album)}</div>
      ${track.progress_ms && track.duration_ms ? `
        <div class="progress-bar">
          <div class="progress-fill" style="width: ${progressPercent}%"></div>
        </div>
        <div class="time-info">
          <span>${this.formatTime(track.progress_ms)}</span>
          <span>${this.formatTime(track.duration_ms)}</span>
        </div>
      ` : ''}
      <div style="margin-top: 12px; color: #6c757d; font-size: 0.9rem;">
        Playing on ${this.escapeHtml(track.device)}
      </div>
    `;
  }

  async generateImage() {
    try {
      this.setState({ error: null, generating: true });
      const response = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/request_image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          options: { width: 800, height: 480 }
        })
      });

      const raw = await response.json();
      // Some gateway layers may wrap the payload inside an "image" field (observed case: { image: { success: true, ... } })
      let data = raw;
      if (!('success' in data) && data.image && typeof data.image === 'object' && 'success' in data.image) {
        console.debug('[SpotifyStatusManager] Detected nested image payload wrapper; flattening');
        data = data.image;
      }

      if (data.success) {
        const mime = data.format === 'png' ? 'image/png' : 'image/jpeg';
        const imageWindow = window.open('', '_blank');
        imageWindow.document.write(`
          <html>
            <head><title>Spotify Status Image</title></head>
            <body style="margin:0; background:#000; display:flex; flex-direction:column; align-items:center; justify-content:center; font-family:system-ui; color:#fff;">
              <div style="position:fixed; top:8px; left:12px; font-size:12px; opacity:0.7;">${mime.toUpperCase()} ${data.width}x${data.height}</div>
              <img src="data:${mime};base64,${data.image}" style="max-width:100%; max-height:100%; object-fit:contain;">
            </body>
          </html>
        `);
      } else {
        const detail = [];
        if (data.reason) detail.push(`reason=${data.reason}`);
        if (data.steps) detail.push(`steps=${data.steps.join('>')}`);
        this.setState({ error: `Failed to generate image: ${data.error || data.message || 'Unknown error'}${detail.length ? ' (' + detail.join(', ') + ')' : ''}` });
        console.warn('Spotify image generation failure payload', { raw, flattened: data });
      }
    } catch (error) {
      console.error('Failed to generate image:', error);
      this.setState({ error: 'Failed to generate image. Please try again.' });
    } finally {
      // Clear generating state
      this.setState({ generating: false });
    }
  }

  formatTime(ms) {
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.floor((ms % 60000) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  startAutoRefresh() {
    // Refresh every 30 seconds
    this.refreshInterval = setInterval(() => {
      this.loadStatus();
    }, 30000);
  }

  disconnectedCallback() {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
    }
  }
}

// Register both legacy and config-specified element names for compatibility
if (!customElements.get('spotify-status-manager')) {
  customElements.define('spotify-status-manager', SpotifyStatusManager);
}
if (!customElements.get('x-spotify-status-manager')) {
  customElements.define('x-spotify-status-manager', SpotifyStatusManager);
}
