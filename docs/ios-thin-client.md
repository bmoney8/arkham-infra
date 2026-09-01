# iOS Thin-Client Deployment Profile — Arkham Cockpit (V6.3.3)

Status: SPEC (authored 2026-09-01, exec 2105943f). No macOS/Xcode host exists in
the mesh; the iOS target is prepared as code + docs and requires a Mac (or
cloud Mac CI) to produce a signed .ipa. Every non-executable artifact is marked
SPEC.

## 1. Tauri v2 iOS project wrapper layout

`tauri ios init` inside `tauri-cockpit-v2/src-tauri` generates:

```
src-tauri/gen/apple/                     # generated Xcode project (SPEC — needs macOS)
  project.xcodeproj/
  ExportOptions.plist
  Assets.xcassets/ (AppIcon, accent color)
  SwiftArkhamCockpitView/ (Swift bootstrap: AppDelegate, ContentView)
src-tauri/tauri.ios.conf.json            # iOS-specific overlay config (SPEC)
```

- Signing: set `DEVELOPMENT_TEAM` (operator Apple ID team placeholder:
  `XXXXXXXXXX`) in the Xcode project; export via `xcodebuild -exportArchive`
  with `ExportOptions.plist` method `ad-hoc` or `development`.
- Rust targets: `aarch64-apple-ios` (device) + `aarch64-apple-ios-sim`
  (simulator); `cargo tauri ios build` orchestrates.
- The `twenty` window URL (`http://100.64.0.4:3020`) is reachable on-device
  only through the Headscale mesh (VPN app / Tailscale iOS with MagicDNS);
  ATS exception for the plain-http mesh origin goes in Info.plist
  (`NSAppTransportSecurity → NSAllowsArbitraryLoads = NO` +
  `NSExceptionDomains` for `100.64.0.4` with `NSExceptionAllowsInsecureHTTPLoads = YES`).

## 2. Secure Enclave / Keychain auth

- Entitlement: `keychain-access-groups = ["<TEAMID>.dev.arkham.cockpit.shared"]`.
- Flow: on first launch generate/collect the mesh bearer token → store via
  `SecItemAdd` (`kSecClassGenericPassword`, service `dev.arkham.cockpit`,
  account `mesh-bearer`, `kSecAttrSynchronizable = NO`,
  `kSecAttrAccessible = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`).
  Subsequent reads via `SecItemCopyMatching`; rotate via
  `SecItemUpdate`. ThisDeviceOnly keeps the mesh token off iCloud Keychain —
  hardware-bound protection comes from the Secure Enclave wrapping the
  item-protection key class (use `biometryCurrentSet` if Face-ID gating is
  desired).
- Token persistence mapping (Directive 3.7 / D4.3):

| Platform | Store | API |
|---|---|---|
| iOS | Keychain (GenericPassword, ThisDeviceOnly) | SecItemAdd/CopyMatching |
| Windows desktop | Credential Manager (or %LOCALAPPDATA% gitignored env) | CredWrite/CredRead |
| Cockpit Rust core | tauri-plugin-store encrypted file fallback | — |

### iOS Shortcuts CANNOT read Keychain (explicit limitation)

Apple Shortcuts has no Keychain action. The meeting-ingest Shortcut therefore
authenticates one of two ways:

1. **Preferred — mesh ACL:** VPN-on-demand (Tailscale iOS) makes the mesh
   reachable; Genos' firewall restricts :8645 to mesh ranges, and the token is
   embedded in the Shortcut's "Text" field (paste once; device-local).
2. **Manual token paste:** same Text-field embed, no VPN (requires Genos :8645
   to be reachable — over mesh only, so in practice option 1).

Never distribute the Shortcut file with a live token inside.

## 3. AVFoundation capture hooks (SPEC)

```swift
let session = AVAudioSession.sharedInstance()
try session.setCategory(.playAndRecord, mode: .spokenAudio,
                        options: [.defaultToSpeaker, .allowBluetooth])
try session.setActive(true)

let settings: [String: Any] = [
  AVFormatIDKey: kAudioFormatMPEG4AAC,          // .m4a, voice-optimized
  AVSampleRateKey: 48_000,
  AVNumberOfChannelsKey: 1,
  AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
]
let recorder = try AVAudioRecorder(url: meetingURL, settings: settings)
recorder.record()
```

**Honest hardware caveat:** iOS exposes NO public API for system-wide audio
loopback (capturing another app's output). Near-equivalents:
ReplayKit broadcast extension (screen + app audio, user-visible broadcast
indicator, user consent per session) or per-app audio hooks only if the call
app implements them. Zero-bot *system* call recording is therefore a Windows
(WASAPI loopback) capability; on iOS the Shortcut records the **microphone**
(or a ReplayKit broadcast for screen-shared meetings).

## 4. Mobile layout viewport

- `index.html`: `<meta name="viewport" content="width=device-width,
  initial-scale=1.0, viewport-fit=cover">` (already in the cockpit UI).
- CSS: pad critical chrome with `env(safe-area-inset-*)` (already in
  `ui/src/index.css`); test notch + home-indicator layouts; `<1000px` width →
  cockpit HUD stacks vertically; Twenty window (mobile) uses Twenty's own
  responsive layout.

## 5. Ingest path (this profile's purpose)

iOS Shortcut → `POST http://100.64.0.5:8645/meeting/ingest` (multipart field
`audio`, header `X-Arkham-Token`) → Genos meeting-ingest.service →
Whisper/Groq BYOK (when key provisioned) → GLM-5.3 Flash synthesis via
OmniRouter → markdown brief. Shortcut workflow definition:
`ios-thin-client/shortcuts-meeting-ingest.plist` (SPEC).
