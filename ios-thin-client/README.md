# iOS Meeting-Ingest Shortcut — quick start (SPEC)

1. On the iPhone (Node Do-S, 100.64.0.6): Shortcuts → + → name it
   "Arkham Meeting Ingest".
2. Add actions in this order (mirrors shortcuts-meeting-ingest.plist):
   Record Audio (or Record Screen for app-audio) → Encode Media (Audio Only
   M4A) → Get Contents of URL:
   - URL `http://100.64.0.5:8645/meeting/ingest`, method POST
   - Header `X-Arkham-Token` = (Text action holding the token)
   - Request Body: File → the encoded m4a, field name **audio**
   → Get Dictionary Value / Speak (response summary).
3. Token source: the `MEETING_INGEST_TOKEN` value provisioned in
   `/etc/systemd/system/meeting-ingest.service` on Genos — request it from the
   operator (never stored in this repo). Paste it ONCE into the Shortcut's
   Text action; Shortcuts cannot read Keychain.
4. Mesh reachability: enable Tailscale iOS (VPN on-demand) so 100.64.0.5
   resolves; :8645 is mesh-scoped and never public.
5. Test: run the Shortcut with a 5-second recording, then check
   `ssh genos 'ls -la ~/meeting-ingest/uploads/'` for the new file.

Notes: filename pattern `meeting-<date>.m4a`; response JSON
`{received:true, upload_id, bytes}`; STT (Whisper/Groq) activates once a
`GROQ_API_KEY` appears in the Genos env — until then uploads are stored and
`/health` reports `stt_configured:false`.
