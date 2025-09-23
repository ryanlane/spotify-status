// x-spotify-status-card Web Component (dashboard widget)
class XSpotifyStatusCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.apiBaseUrl = this.getApiBaseUrl();
    this.pollIntervalMs = 30000;
    // track last updated time (client-side)
    this._lastUpdated = null;
  }

  getApiBaseUrl() {
    return window.mimirServerBaseUrl || window.location.origin;
  }

  connectedCallback() {
    this.renderSkeleton();
    this.fetchData();
    this.startPolling();
    // Observe host prop changes like photo frame card pattern
    const observer = new MutationObserver(m => {
      for (const mu of m) {
        if (mu.type === 'attributes' && mu.attributeName === 'data-hostprops') {
          this.render();
        }
      }
    });
    observer.observe(this, { attributes: true });
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
  }

  startPolling() {
    this._timer = setInterval(() => this.fetchData(), this.pollIntervalMs);
  }

  async fetchData() {
    try {
      const resp = await fetch(`${this.apiBaseUrl}/api/channels/com.spotify.status/current-track`, { credentials: 'include' });
      const data = await resp.json();
      this._data = data;
      this._lastUpdated = new Date().toISOString();
      this.render();
    } catch (e) {
      console.error('Spotify status card fetch failed', e);
      this._data = { error: 'fetch_failed' };
      this._lastUpdated = new Date().toISOString();
      this.render();
    }
  }

  renderSkeleton() {
    this.shadowRoot.innerHTML = `
      <style>${this.styles()}</style>
      <div class="card loading">
        <div class="header-row">
          <h3>Spotify Status</h3>
          <span class="badge">Loading...</span>
        </div>
        <div class="placeholder-image"></div>
      </div>
    `;
  }

  styles() {
    return `
      :host { display:block; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
      .card { border:1px solid #e1e5e9; border-radius:8px; padding:16px; background:#fff; box-shadow:0 2px 8px rgba(0,0,0,0.05); max-width:400px; }
      .header-row { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
      h3 { margin:0; font-size:1.05rem; }
      .badge { background:#1db954; color:#fff; padding:2px 8px; border-radius:12px; font-size:0.65rem; letter-spacing:0.5px; }
      .error { background:#f8d7da; color:#721c24; padding:8px; border-radius:4px; font-size:0.75rem; margin-top:8px; }
      .art-wrapper { position:relative; width:100%; padding-top:100%; background:#f8f9fa; border-radius:6px; overflow:hidden; }
      img.art { position:absolute; top:0; left:0; width:100%; height:100%; object-fit:cover; }
      .meta { margin-top:10px; font-size:0.8rem; line-height:1.3; }
      .title { font-weight:600; font-size:0.9rem; color:#212529; }
      .artist { color:#495057; }
      .album { color:#868e96; }
      .device { margin-top:6px; color:#6c757d; font-size:0.7rem; }
      .progress { margin-top:8px; height:4px; background:#e9ecef; border-radius:2px; overflow:hidden; }
      .progress > div { height:100%; background:#1db954; transition:width 1s linear; }
      button.refresh { margin-top:12px; background:#1db954; border:none; color:#fff; padding:6px 12px; border-radius:4px; cursor:pointer; font-size:0.7rem; }
      button.refresh:hover { background:#1ed760; }
      .placeholder-image { width:100%; height:180px; background: repeating-linear-gradient(45deg,#f1f3f5,#f1f3f5 10px,#e9ecef 10px,#e9ecef 20px); border-radius:6px; }
      .empty { font-size:0.8rem; color:#6c757d; margin:12px 0 4px; }
      .footer { margin-top:8px; color:#6c757d; font-size:0.7rem; text-align:right; }
    `;
  }

  formatTime(ms) {
    if (!ms && ms !== 0) return '--:--';
    const m = Math.floor(ms / 60000);
    const s = Math.floor((ms % 60000) / 1000).toString().padStart(2,'0');
    return `${m}:${s}`;
  }

  render() {
    const d = this._data || {};
    const hostProps = JSON.parse(this.getAttribute('data-hostprops') || '{}');
    const track = d.track;
    let artUrl = track && track.album_art_url ? track.album_art_url : null;

    let progressPct = 0;
    if (track && track.progress_ms && track.duration_ms) {
      progressPct = Math.min(100, (track.progress_ms / track.duration_ms) * 100);
    }

    const lastUpdatedMarkup = this._lastUpdated ? `<div class="footer">Updated ${new Date(this._lastUpdated).toLocaleTimeString()}</div>` : '';

    this.shadowRoot.innerHTML = `
      <style>${this.styles()}</style>
      <div class="card">
        <div class="header-row">
          <h3>Spotify Status</h3>
          <span class="badge">${d.success === false ? 'ERR' : (track ? 'PLAYING' : 'IDLE')}</span>
        </div>
        ${d.error ? `<div class="error">${d.error}</div>` : ''}
        <div class="art-wrapper">
          ${artUrl ? `<img class="art" src="${artUrl}" alt="Album Art"/>` : ''}
        </div>
        ${track ? `
          <div class="meta">
            <div class="title" title="${track.name||''}">${track.name||'Unknown'}</div>
            <div class="artist" title="${track.artist||''}">${track.artist||'Unknown Artist'}</div>
            <div class="album" title="${track.album||''}">${track.album||''}</div>
            <div class="progress"><div style="width:${progressPct}%"></div></div>
            <div class="device">${track.device ? 'On ' + track.device : ''} · ${this.formatTime(track.progress_ms||0)} / ${this.formatTime(track.duration_ms||0)}</div>
          </div>
        ` : `<div class="empty">No music playing</div>`}
        <button class="refresh">Refresh</button>
        ${lastUpdatedMarkup}
      </div>
    `;

    this.shadowRoot.querySelector('button.refresh')?.addEventListener('click', () => this.fetchData());
  }
}

if (!customElements.get('x-spotify-status-card')) {
  customElements.define('x-spotify-status-card', XSpotifyStatusCard);
}
