# AgentDVR for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration that surfaces your
[AgentDVR](https://www.ispyconnect.com/) NVR **recordings** inside Home Assistant:

- 📁 **Media browser** — browse recordings per camera and play them from the **Media** panel.
- 🧱 **Dashboard card** — a bundled Lovelace card (`agentdvr-recordings-card`) that shows a
  thumbnail grid of recent recordings and plays them inline on any dashboard.

## Requirements

- Home Assistant 2024.12 or newer.
- An AgentDVR server reachable from Home Assistant (and from the browsers/devices you view
  dashboards on — see [Networking](#networking)).

## Installation

Install with **HACS** (recommended — gives you one-click updates) or **manually**. Either
way, you then add the integration from the Home Assistant UI (see
[Configuration](#configuration)).

### Option A — HACS (custom repository)

1. Make sure [HACS](https://hacs.xyz/) is installed and set up.
2. In Home Assistant, go to **HACS** (sidebar).
3. Click the **⋮** menu (top-right) → **Custom repositories**.
4. Add the repository:
   - **Repository:** `https://github.com/ahmedjafri/ha-agentdvr`
   - **Type:** `Integration`
   - Click **Add**.
5. Search HACS for **AgentDVR**, open it, and click **Download**.
6. **Restart Home Assistant** (Settings → System → ⋮ → Restart).
7. Continue to [Configuration](#configuration).

To update later, HACS will show an update for **AgentDVR** whenever a new version is
released — download it and restart.

### Option B — Manual

1. Download this repository (GitHub → **Code** → **Download ZIP**, or `git clone`).
2. Copy the `custom_components/agentdvr/` folder into your Home Assistant configuration
   directory so the result looks like:

   ```text
   <config>/
   └── custom_components/
       └── agentdvr/
           ├── __init__.py
           ├── manifest.json
           ├── media_source.py
           └── … (the rest of the files)
   ```

   `<config>` is the folder that contains your `configuration.yaml`. If a
   `custom_components` folder doesn't exist yet, create it. Common ways to get the files
   there: the **Samba share**, **SSH & Web Terminal**, or **Studio Code Server** add-ons.
3. **Restart Home Assistant.**
4. Continue to [Configuration](#configuration).

To update later, replace the `custom_components/agentdvr/` folder with the newer version
and restart.

## Configuration

1. **Settings → Devices & Services → Add Integration → AgentDVR.**
2. Enter your AgentDVR **host** (e.g. `agentdvr.lan`) and **port** (default `8090`).
   Username/password are optional — fill them in only if your AgentDVR requires login.

The connection is validated against AgentDVR's `getObjects` endpoint before the entry is
created.

## Viewing recordings

### Media panel

Open **Media** in the sidebar → **AgentDVR Recordings** → pick a camera → pick a clip.
Recordings are listed newest-first with thumbnails and a title like
`2026-07-07 15:48:33 · 0:15 · person, detected`.

### Dashboard card

Add a card to any dashboard:

```yaml
type: custom:agentdvr-recordings-card
camera: all      # "all" (default) or a specific camera object id, e.g. 5
count: 12        # how many recordings to show (default 12)
columns: 3       # grid columns (default 3)
title: Recordings
```

The card is registered automatically by the integration — no manual Lovelace resource is
needed. Click a thumbnail to play it inline.

## Caveats

- **`.mkv` recordings are not playable in a browser.** AgentDVR records some clips as
  Matroska (`.mkv`), which browsers can't decode. They are still listed (flagged
  `(mkv – not playable)` / dimmed with an `MKV` badge). To make everything playable, set
  AgentDVR to record **MP4**.
- **No seeking.** AgentDVR's `streamFile.cgi` does not advertise HTTP range support, so
  clips play start-to-finish without a working scrub bar.
- <a id="networking"></a>**Networking.** Thumbnails and recording streams are proxied
  through Home Assistant (same-origin, signed URLs) — your browser only ever talks to Home
  Assistant, so it does **not** need to reach the AgentDVR host directly, and any AgentDVR
  credentials stay server-side. Home Assistant itself must be able to reach the AgentDVR
  host/port on your LAN.

## Debugging

```yaml
logger:
  logs:
    custom_components.agentdvr: debug
```

## License

MIT — see [LICENSE](LICENSE).
