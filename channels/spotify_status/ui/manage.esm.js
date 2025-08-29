// Spotify Status Manager Web Component for Mimir Platform v2.5
class SpotifyStatusManager extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    
    this.state = {
      currentTrack: null,
      isConnected: false,
      isLoading: true,
      error: null
    };

    this.apiBaseUrl = this.getApiBaseUrl();
  }

  getApiBaseUrl() {
    return window.mimirServerBaseUrl || window.location.origin;
  }

  async connectedCallback() {
    await this.loadStatus();
    this.render();
    this.startAutoRefresh();
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
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        .container {
          max-width: 600px;
          margin: 0 auto;
          padding: 24px;
        }
        .card {
          background: white;
          border-radius: 12px;
          padding: 32px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .header {
          text-align: center;
          margin-bottom: 32px;
        }
        .header h1 {
          color: #1db954;
          margin: 0 0 8px 0;
        }
        .status {
          display: flex;
          align-items: center;
          margin-bottom: 24px;
          padding: 16px;
          background: #f8f9fa;
          border-radius: 8px;
        }
        .status-dot {
          width: 12px;
          height: 12px;
          border-radius: 50%;
          margin-right: 12px;
        }
        .status-dot.connected { background: #1db954; }
        .status-dot.disconnected { background: #dc3545; }
        .track-info {
          background: ${this.state.currentTrack ? '#f0fdf4' : '#f8f9fa'};
          border: 1px solid ${this.state.currentTrack ? '#1db954' : '#dee2e6'};
          border-radius: 8px;
          padding: 20px;
          margin-bottom: 24px;
        }
        .track-title {
          font-size: 1.2rem;
          font-weight: 600;
          margin-bottom: 8px;
          color: #212529;
        }
        .track-artist {
          color: #6c757d;
          margin-bottom: 4px;
        }
        .track-album {
          color: #9ca3af;
          font-size: 0.9rem;
          margin-bottom: 12px;
        }
        .progress-bar {
          width: 100%;
          height: 4px;
          background: #e9ecef;
          border-radius: 2px;
          margin: 12px 0;
          overflow: hidden;
        }
        .progress-fill {
          height: 100%;
          background: #1db954;
          transition: width 1s linear;
        }
        .time-info {
          display: flex;
          justify-content: space-between;
          font-size: 0.8rem;
          color: #6c757d;
        }
        .controls {
          display: flex;
          gap: 12px;
          justify-content: center;
        }
        .btn {
          background: #1db954;
          color: white;
          border: none;
          border-radius: 6px;
          padding: 10px 20px;
          cursor: pointer;
          font-size: 0.9rem;
          transition: background 0.2s;
        }
        .btn:hover {
          background: #1ed760;
        }
        .btn-secondary {
          background: #6c757d;
        }
        .btn-secondary:hover {
          background: #5a6268;
        }
        .loading {
          text-align: center;
          padding: 40px;
          color: #6c757d;
        }
        .error {
          background: #f8d7da;
          border: 1px solid #f5c6cb;
          color: #721c24;
          padding: 16px;
          border-radius: 8px;
          margin-top: 16px;
        }
      </style>
      
      <div class="container">
        <div class="card">
          <div class="header">
            <h1>🎵 Spotify Status</h1>
            <p>Monitor your currently playing track</p>
          </div>
          
          ${this.state.isLoading ? `
            <div class="loading">
              <h3>Loading Spotify status...</h3>
            </div>
          ` : `
            <div class="status">
              <div class="status-dot ${this.state.isConnected ? 'connected' : 'disconnected'}"></div>
              <span>${this.getStatusText()}</span>
            </div>
            
            <div class="track-info">
              ${this.renderTrackInfo()}
            </div>
            
            <div class="controls">
              <button class="btn" id="refresh-btn">🔄 Refresh</button>
              <button class="btn" id="generate-btn">🖼️ Generate Image</button>
            </div>
            
            ${this.state.error ? `
              <div class="error">${this.state.error}</div>
            ` : ''}
          `}
        </div>
      </div>
    `;

    this.attachEventListeners();
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

  attachEventListeners() {
    const refreshBtn = this.shadowRoot.getElementById('refresh-btn');
    const generateBtn = this.shadowRoot.getElementById('generate-btn');

    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => this.loadStatus());
    }

    if (generateBtn) {
      generateBtn.addEventListener('click', () => this.generateImage());
    }
  }

  async generateImage() {
    try {
      const response = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/request_image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          options: { width: 800, height: 480 }
        })
      });

      const data = await response.json();

      if (data.success) {
        // Open image in new window
        const imageWindow = window.open('', '_blank');
        imageWindow.document.write(`
          <html>
            <head><title>Spotify Status Image</title></head>
            <body style="margin:0; background:#000; display:flex; align-items:center; justify-content:center;">
              <img src="data:image/jpeg;base64,${data.image}" style="max-width:100%; max-height:100%;">
            </body>
          </html>
        `);
      } else {
        this.setState({ error: `Failed to generate image: ${data.error || 'Unknown error'}` });
      }
    } catch (error) {
      console.error('Failed to generate image:', error);
      this.setState({ error: 'Failed to generate image. Please try again.' });
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

customElements.define('spotify-status-manager', SpotifyStatusManager);
