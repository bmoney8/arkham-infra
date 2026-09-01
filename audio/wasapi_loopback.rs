// Arkham Cockpit — Windows WASAPI loopback capture (Directive 5.4, V6.3.3)
//
// Zero-bot desktop call recording: captures whatever Windows is playing on a
// render endpoint (the call audio of Zoom/Teams/Meet/Phone-link calls) WITHOUT
// any in-meeting bot joining the call.
//
// MECHANISM: WASAPI loopback = open a render endpoint device with
// AUDCLNT_STREAMFLAGS_LOOPBACK and read from it like a capture client. Windows
// taps the post-mix stream of that output device. To record the * microphone *
// too (your own voice), a real capture client on the default capture device is
// opened in parallel and both PCM streams are mixed in i16 space.
//
// STATUS: SPEC / UNBUILT. This module is authored on Node .4 (WSL2) where the
// WASAPI COM surface does not exist; the build-runner on the Psykos Windows
// host compiles it into the cockpit's src-tauri/src/audio/ with:
//   cargo add windows --features Win32_Media_Audio,Win32_System_Com,
//       Win32_Media_KernelStreaming,Win32_Devices_Properties
// Verified-by-build criteria (build runner executes, not assumed):
//   [ ] cargo check --target x86_64-pc-windows-msvc exits 0 on the host
//   [ ] `arkham-cockpit --record-loopback --duration 5` produces a .wav whose
//       header bytes match RIFF/WAVE and data length > 0 while media plays
//   [ ] zero-error startup logged in the cockpit trace
//
// Output format: 48 kHz stereo 16-bit PCM WAV (WASAPI shared-mode mix format
// is typically float32 — converted to i16 on write).

use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct LoopbackConfig {
    /// Render device friendly-name substring; None = default render device.
    pub device_hint: Option<String>,
    /// Mix in the default capture (microphone) device as well.
    pub include_mic: bool,
    /// Where the WAV lands (cockpit default: %LOCALAPPDATA%\ArkhamCockpit\recordings).
    pub out_path: PathBuf,
    /// Hard stop; 0 = record until stop_loopback() is called.
    pub max_seconds: u32,
}

impl Default for LoopbackConfig {
    fn default() -> Self {
        Self {
            device_hint: None,
            include_mic: true,
            out_path: PathBuf::from("recordings"),
            max_seconds: 0,
        }
    }
}

#[derive(Debug)]
pub struct WavWriter {
    file: std::fs::File,
    data_bytes: u32,
}

impl WavWriter {
    /// 16-bit PCM WAV, 48 kHz, N channels. Header patched on drop/finalize.
    pub fn create(path: &std::path::Path, channels: u16, sample_rate: u32) -> std::io::Result<Self> {
        use std::io::Write;
        let mut file = std::fs::File::create(path)?;
        let mut h = [0u8; 44];
        h[..4].copy_from_slice(b"RIFF");
        h[8..12].copy_from_slice(b"WAVE");
        h[12..16].copy_from_slice(b"fmt ");
        h[16..20].copy_from_slice(&16u32.to_le_bytes());
        h[20..22].copy_from_slice(&1u16.to_le_bytes()); // PCM
        h[22..24].copy_from_slice(&channels.to_le_bytes());
        h[24..28].copy_from_slice(&sample_rate.to_le_bytes());
        let byte_rate = sample_rate * channels as u32 * 2;
        h[28..32].copy_from_slice(&byte_rate.to_le_bytes());
        let block_align = channels * 2;
        h[32..34].copy_from_slice(&block_align.to_le_bytes());
        h[34..36].copy_from_slice(&16u16.to_le_bytes());
        h[36..40].copy_from_slice(b"data");
        file.write_all(&h)?;
        Ok(Self { file, data_bytes: 0 })
    }

    pub fn write_i16(&mut self, samples: &[i16]) -> std::io::Result<()> {
        use std::io::Write;
        let mut buf = Vec::with_capacity(samples.len() * 2);
        for s in samples {
            buf.extend_from_slice(&s.to_le_bytes());
        }
        self.data_bytes += buf.len() as u32;
        self.file.write_all(&buf)
    }

    pub fn finalize(mut self) -> std::io::Result<u32> {
        use std::io::{Write, Seek, SeekFrom};
        let riff = 36 + self.data_bytes;
        self.file.seek(SeekFrom::Start(4))?;
        self.file.write_all(&riff.to_le_bytes())?;
        self.file.seek(SeekFrom::Start(40))?;
        self.file.write_all(&self.data_bytes.to_le_bytes())?;
        self.file.flush()?;
        Ok(self.data_bytes)
    }
}

/// f32 [-1.0, 1.0] (WASAPI shared-mode float mix) -> i16 with clipping.
#[inline]
pub fn f32_to_i16(x: f32) -> i16 {
    let v = (x * 32767.0).round();
    if v > 32767.0 { 32767 } else if v < -32768.0 { -32768 } else { v as i16 }
}

/// Naive N:1 downmix for merging mic into loopback when channel counts differ.
pub fn mix_i16(dst: &mut [i16], add: &[i16]) {
    let n = dst.len().min(add.len());
    for i in 0..n {
        let sum = (dst[i] as i32) + (add[i] as i32);
        dst[i] = sum.clamp(-32768, 32767) as i16;
    }
}

// NOTE(build-runner): the actual WASAPI calls below are gated to windows hosts.
// Skeleton kept compile-plausible on non-windows so `cargo check` on .4 also passes.
#[cfg(all(target_os = "windows", feature = "wasapi-loopback"))]
pub mod wasapi {
    // Real implementation on the Psykos host uses:
    //   windows::Win32::Media::Audio::{
    //       CoCreateInstance, MMDeviceEnumerator, IMMDeviceEnumerator,
    //       IAudioClient, AUDCLNT_STREAMFLAGS_LOOPBACK, AUDCLNT_SHAREMODE_SHARED,
    //       WAVE_FORMAT_EXTENSIBLE, eRender, eCapture, GetDefaultAudioEndpoint,
    //   }
    // Loopback recipe:
    //   1. enumerator.GetDefaultAudioEndpoint(eRender, eConsole)  (or FindDevice by hint)
    //   2. client = device.Activate::<IAudioClient>(CLSCTX_ALL, None)
    //   3. client.GetMixFormat(&mut fmt)  — usually 48k stereo float32
    //   4. client.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
    //                        20_000_0 /*20ms*/, 0, fmt, None)
    //   5. capture = client.GetService::<IAudioCaptureClient>()
    //   6. client.Start(); loop { GetNextPacketSize / GetBuffer / f32_to_i16 / write }
    //   7. Mic leg: identical but GetDefaultAudioEndpoint(eCapture, eConsole) with
    //      plain shared capture (no LOOPBACK flag), mix_i16 into the loopback stream.
    // TODO(build-runner): paste the full COM block here per the recipe above and
    // satisfy the three verified-by-build criteria in the module header.
}

#[cfg(not(all(target_os = "windows", feature = "wasapi-loopback")))]
pub mod wasapi {
    use super::LoopbackConfig;
    pub fn record_loopback(_cfg: LoopbackConfig) -> anyhow::Result<std::path::PathBuf> {
        anyhow::bail!("wasapi-loopback feature requires the Windows host build (Psykos build-runner)")
    }
}

/// Cockpit-facing entry: spawn a background thread that records until
/// stop is signalled or max_seconds elapses; returns the WAV path.
pub fn start_meeting_recording(cfg: LoopbackConfig) -> Result<PathBuf, String> {
    wasapi::record_loopback(cfg).map_err(|e| e.to_string())
}

pub fn stop_loopback() {
    // AtomicBool consumed by the worker thread (see TODO(build-runner)).
}
