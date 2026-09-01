# ADR-0002: Tauri v2 Cockpit Workspace & 4-Instance HUD Deployment Matrix

Date: 2026-09-01 · Packet: V6.3.3 · Exec: 2105943f (uuid8)
Status: scaffolded (build pending on Psykos Windows-host runner)

## Context

V6.3.3 directs an authoritative Tauri v2 workspace on Node .4 (Psykos WSL2,
100.64.0.4) following Codeg patterns (Rust backend core + React frontend),
dual-platform build profiles (portable Windows .exe + iOS thin client), and a
4-tier HUD deployment matrix. A working Tauri v2 app already exists in the
fleet: `/mnt/c/Arkham/hermes-hud` (bmoney8/hermes-hud, commits 3b57981 →
90ab8dc → f2588be).

## Decision

### 1. Workspace

`arkham-infra/tauri-cockpit-v2/` — app **Arkham Cockpit**
(`dev.arkham.cockpit`):

- `src-tauri/` Rust core: multi-window shell, system tray
  (Show Cockpit / Show Twenty CRM / Hide Twenty CRM / Quit),
  tauri-plugin-window-state (remembers per-window geometry → window snapping
  persistence), tauri-plugin-shell.
- Windows: `main` (460x760 cockpit HUD, alwaysOnTop, frameless,
  `dragDropEnabled:false`) + `twenty` (1280x840 frameless, loads
  `http://100.64.0.4:3020` directly — no browser tab overhead).
- `ui/` React 18 + Vite 5 + Tailwind, port 1420; dark theme; OmniRouter
  reachability card; Twenty window launcher; viewport-fit=cover for the iOS
  profile.
- `ui/src-tauri/tauri.conf.json` second config mirrors the hermes-hud
  frontend-dogfood pattern (frontendDist ../dist, no windows array).
- Placeholder 32x32 icon at `src-tauri/icons/icon.png` (replace at branding
  pass).

Known trap carried over from V5.7: **every window sets
`dragDropEnabled:false`** — Tauri v2 native drag-drop intercepts OS file drops
before the DOM and silently kills HTML5 drop handlers.

### 2. Deprecation finding (verified, not assumed)

Filesystem sweep of `/home/bryce/Arkham` and `/mnt/c/Arkham` on 2026-09-01
found **zero Tauri v1 stubs** anywhere in the fleet. Deprecation of v1 stubs is
**N/A**; this scaffold is the first Tauri v2 workspace. The only pre-existing
Tauri code is hermes-hud, already v2.

### 3. 4-instance deployment matrix

| Instance | Host | Persona | Artifact |
|---|---|---|---|
| 1 — Maiko HUD | Node .7 (100.64.0.7) | Makima Mobile | hermes-hud.exe (deployed V5.9.2; scheduled task HermesHUD = Ready, process live) |
| 2 — Psykos HUD | Node .4 / .2 | Makima Core | arkham-cockpit.exe (this scaffold) — desktop command cockpit + Twenty CRM shell |
| 3 — Ventoy HUD | F:\fsociety-field-kit | fsociety diverse | portable fsociety-hud (V6.3.2-A staged; docker-compose.yml hooks present in fsociety-hermes) |
| 4 — Lexar HUD | E:\apps\hermes-hud | Makima diverse | additive-only; persistence.dat (1 GiB, mtime live), light-yagami.yaml, aider.conf.yml, osint/ all verified intact |

### 4. Dual-platform build profiles

- **Desktop (authoritative):** portable Windows .exe built by the Psykos
  Windows-host cargo toolchain (WSL cannot cross-compile — `cc-rs` needs
  MSVC `lib.exe`), build command pattern:
  `ssh psykos 'powershell.exe -NoProfile -Command "Set-Location C:\Arkham\arkham-infra\tauri-cockpit-v2\src-tauri; cargo build --release"'`
  after the tree is mirrored to C:. Portable = single exe, no installer, no
  registry writes; state lives beside the exe / in %LOCALAPPDATA%.
- **Mobile:** iOS thin-client wrapper (`tauri ios init` → src-tauri/gen/apple),
  Keychain/Secure Enclave token storage, AVFoundation mic capture.
  Spec + Shortcut workflow: `ios-thin-client/` and `docs/ios-thin-client.md`.
  Honest limitation recorded there: iOS has no public system-audio loopback
  API; capture is mic-only (or ReplayKit broadcast for screen+audio).

## Consequences

- Build-runner must `npm install` in ui/ and run the two cargo checks before
  any DONE claim on the cockpit binary.
- The `twenty` window is capability-scoped (window:all, shell:allow-open only)
  in `src-tauri/capabilities/default.json`.
- Meeting-audio directive (V6.3.3 D5) lands in `src-tauri/src/audio/` from
  `arkham-infra/audio/wasapi_loopback.rs` (SPEC, verified-by-build criteria in
  the module header).
