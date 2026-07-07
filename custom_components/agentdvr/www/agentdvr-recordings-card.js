/*
 * AgentDVR Recordings Card
 *
 * A Lovelace card that browses AgentDVR recordings through Home Assistant's
 * Media Source and plays them inline. Bundled with (and auto-registered by)
 * the `agentdvr` integration.
 */

const CARD_VERSION = "0.1.0";
const ROOT_ID = "media-source://agentdvr";

class AgentDVRRecordingsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._items = null; // cached recording children
    this._loading = false;
    this._error = null;
    this._selectedUrl = null; // currently playing resolved URL
    this._scaffolded = false;
  }

  // -------------------------------------------------------------- config
  setConfig(config) {
    const count = Number(config.count ?? 12);
    const columns = Number(config.columns ?? 3);
    if (!Number.isFinite(count) || count < 1) {
      throw new Error("`count` must be a positive number");
    }
    if (!Number.isFinite(columns) || columns < 1) {
      throw new Error("`columns` must be a positive number");
    }
    this._config = {
      camera: config.camera ?? "all",
      count,
      columns,
      title: config.title ?? "AgentDVR Recordings",
    };
    this._items = null; // force reload on config change
    this._selectedUrl = null;
    this._scaffolded = false;
    this._buildSkeleton();
  }

  set hass(hass) {
    const first = this._hass === null;
    this._hass = hass;
    if (this._config && (first || this._items === null)) {
      this._loadRecordings();
    }
  }

  getCardSize() {
    const rows = Math.ceil(this._config.count / this._config.columns);
    return 1 + rows * 2;
  }

  static getStubConfig() {
    return {
      type: "custom:agentdvr-recordings-card",
      camera: "all",
      count: 12,
      columns: 3,
    };
  }

  // ---------------------------------------------------------------- data
  async _browse(mediaContentId) {
    return this._hass.connection.sendMessagePromise({
      type: "media_source/browse_media",
      media_content_id: mediaContentId,
    });
  }

  async _loadRecordings() {
    if (!this._hass) return;
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const root = await this._browse(ROOT_ID);
      let cameras = (root.children || []).filter((c) => c.can_expand);
      if (this._config.camera !== "all") {
        const want = String(this._config.camera);
        cameras = cameras.filter(
          (c) =>
            c.media_content_id.endsWith(`/${want}`) ||
            c.media_content_id.endsWith(`/${want}/2`) ||
            c.title === want
        );
      }
      const folders = await Promise.all(
        cameras.map((cam) => this._browse(cam.media_content_id))
      );
      const recs = folders.flatMap((node) => node.children || []);
      // Newest-first within each camera; interleave by concatenation for v0.1.
      this._items = recs.slice(0, this._config.count);
    } catch (err) {
      this._error = (err && err.message) || String(err);
      this._items = [];
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _play(child) {
    if (!child.can_play) return;
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "media_source/resolve_media",
        media_content_id: child.media_content_id,
      });
      // res = { url, mime_type }; our media_source returns an absolute
      // streamFile.cgi URL the browser can load directly.
      this._selectedUrl = res.url;
      this._render();
      const video = this.shadowRoot.querySelector("video");
      if (video) {
        video.src = res.url;
        video.play().catch(() => {});
        video.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } catch (err) {
      this._error = (err && err.message) || String(err);
      this._render();
    }
  }

  // ---------------------------------------------------------------- view
  _buildSkeleton() {
    if (this._scaffolded) return;
    this._scaffolded = true;
    const cols = this._config.columns;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px; }
        .header {
          font-size: 1.1rem; font-weight: 500;
          color: var(--primary-text-color); margin-bottom: 8px;
        }
        .grid {
          display: grid; gap: 8px;
          grid-template-columns: repeat(var(--advr-cols, ${cols}), 1fr);
        }
        @media (max-width: 480px) { .grid { --advr-cols: 2; } }
        .tile {
          position: relative; cursor: pointer; border-radius: 8px;
          overflow: hidden; aspect-ratio: 16 / 9;
          background: var(--secondary-background-color);
        }
        .tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .tile .caption {
          position: absolute; bottom: 0; left: 0; right: 0;
          padding: 4px 6px; font-size: 0.72rem; line-height: 1.2;
          color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.75));
        }
        .tile.disabled { cursor: not-allowed; opacity: .55; }
        .badge {
          position: absolute; top: 4px; right: 4px; font-size: 0.62rem;
          padding: 1px 5px; border-radius: 4px; color: #fff;
          background: var(--error-color, #db4437);
        }
        .player { margin-bottom: 10px; }
        .player[hidden] { display: none; }
        .player video { width: 100%; border-radius: 8px; background: #000; }
        .state {
          padding: 16px; text-align: center;
          color: var(--secondary-text-color);
        }
        .state.error { color: var(--error-color, #db4437); }
        .state[hidden] { display: none; }
      </style>
      <ha-card>
        <div class="header"></div>
        <div class="player" hidden><video controls playsinline></video></div>
        <div class="grid"></div>
        <div class="state" hidden></div>
      </ha-card>
    `;
    this._elHeader = this.shadowRoot.querySelector(".header");
    this._elGrid = this.shadowRoot.querySelector(".grid");
    this._elState = this.shadowRoot.querySelector(".state");
    this._elPlayer = this.shadowRoot.querySelector(".player");
    this._render();
  }

  _render() {
    if (!this._scaffolded) return;
    this._elHeader.textContent = this._config.title;

    // Player.
    this._elPlayer.hidden = !this._selectedUrl;

    // State line (loading / error / empty).
    let stateText = "";
    let isError = false;
    if (this._loading) {
      stateText = "Loading recordings…";
    } else if (this._error) {
      stateText = `Error: ${this._error}`;
      isError = true;
    } else if (this._items && this._items.length === 0) {
      stateText = "No recordings found.";
    }
    this._elState.hidden = stateText === "";
    this._elState.textContent = stateText;
    this._elState.classList.toggle("error", isError);

    // Grid.
    this._elGrid.textContent = "";
    for (const child of this._items || []) {
      const tile = document.createElement("div");
      tile.className = "tile" + (child.can_play ? "" : " disabled");

      if (child.thumbnail) {
        const img = document.createElement("img");
        img.src = child.thumbnail;
        img.loading = "lazy";
        img.alt = child.title || "";
        tile.appendChild(img);
      }

      if (!child.can_play) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "MKV";
        badge.title = "Not playable in a browser";
        tile.appendChild(badge);
      }

      const caption = document.createElement("div");
      caption.className = "caption";
      caption.textContent = child.title || "";
      tile.appendChild(caption);

      if (child.can_play) {
        tile.addEventListener("click", () => this._play(child));
      }
      this._elGrid.appendChild(tile);
    }
  }
}

customElements.define("agentdvr-recordings-card", AgentDVRRecordingsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "agentdvr-recordings-card",
  name: "AgentDVR Recordings",
  description: "Browse and play AgentDVR NVR recordings from Media Source.",
  preview: false,
  documentationURL: "https://github.com/ahmedjafri/ha-agentdvr",
});

console.info(
  `%c AGENTDVR-RECORDINGS-CARD %c v${CARD_VERSION} `,
  "color:white;background:#039be5",
  "color:#039be5;background:white"
);
