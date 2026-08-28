# Voice Gateway → Makima HUD Integration (V5.5)

## Architecture

```
Voice Input (User speaks)
    |
    v
Voice Engine (Genos 100.64.0.5:8020 or Saitama 100.64.0.3:8020)
    |  [Piper TTS: 5 profiles (jessica_rabbit, khaleesi, scottish, jenny_dioco, kathleen_turner)]
    |  [Groq Whisper STT passthrough via /v1/audio/transcriptions]
    v
OmniRouter Gateway (Saitama 100.64.0.3:8000)
    |  [BYOK Groq STT key staged on Saitama]
    v
Makima Dashboard (Psykos WSL2 100.64.0.4:8010)
    |  [GET /api/voices, POST /api/tts]
    v
Makima Desktop HUD (Psykos Host 100.64.0.2:3000)
    |  [Tauri native app — voice input widget]
    v
Response (text + voice toggle)
```

## Bidirectional Audio Flow

### Voice Input (STT → Text)
1. User clicks mic button in HUD (:3000)
2. Browser captures audio (Web Audio API / MediaRecorder)
3. Audio sent to Groq Whisper STT endpoint:
   - Via OmniRouter passthrough: `POST http://100.64.0.3:8000/v1/audio/transcriptions`
   - Or direct: `POST http://100.64.0.5:8020/v1/audio/transcriptions` (Genos)
4. Transcription returned as text → fed into chat input

### Voice Output (Text → TTS)
1. LLM generates text response
2. Text sent to TTS endpoint:
   - Primary: `POST http://100.64.0.5:8020/v1/audio/speech` (Genos, profile ID)
   - Fallback: `POST http://100.64.0.3:8020/v1/audio/speech` (Saitama)
3. WAV audio returned → played in HUD

## Profile Routing (Critical)
- **Send PROFILE IDs, NOT raw model names** to the voice engine
- Profile IDs: jessica_rabbit, khaleesi, scottish, jenny_dioco, kathleen_turner
- Unknown profile → silent fallback (broken — see V5.4.1 fix)
- Client-side ID↔model translation is a BUG MAGNET

## Voice Toggling
- Support multiple active profiles without enforcing rigid single-primary
- User can switch voice mid-conversation
- HUD shows profile selector dropdown (mirrors Makima Dashboard :8010 voices)
- Each profile preserves its tone-lock parameters (e.g., jessica_rabbit: speed=0.90, pitch=0.90)

## Low-Latency Groq STT Pipeline
- Groq Whisper provides <1s transcription on the mesh
- BYOK key staged on Saitama only (0600, never in repo)
- OmniRouter gateway proxies STT passthrough (`/v1/audio/transcriptions`)
- Direct Genos :8020 is lower latency (no gateway hop)

## Mesh Binding (100.64.0.3:8020 → 100.64.0.2:3000)
- Voice engine on Genos/Saitama serves over the Headscale mesh
- HUD on Psykos Host connects via mesh IP (no public exposure required)
- UFW rules: `100.64.0.0/10 → 8020` (both Genos and Saitama)

## Implementation Status
- [x] Voice engine live on Genos :8020 (5 profiles, tone-lock verified)
- [x] Voice engine live on Saitama :8020 (CPU Piper fallback)
- [x] Groq STT passthrough configured on OmniRouter
- [x] Makima Dashboard :8010 voice dropdown + TTS integration
- [x] Hermes profile TTS wired (makima-voice.sh → Genos primary)
- [ ] HUD (:3000) voice input widget — requires Tauri frontend changes
- [ ] Bidirectional audio streaming — requires WebSocket/IPC addition
- [ ] Profile switcher in HUD — UI component pending

## Blockers
1. HUD (:3000) is the Tauri desktop app — adding voice input requires modifying the Rust/TypeScript frontend code
2. WebSocket support not yet in the Hermes backend (`hermes-backend.service` on .2:9119)
3. Browser audio capture (MediaRecorder API) works in web apps but requires Tauri WebView permission configuration
