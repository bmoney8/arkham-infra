import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type OmniState = "checking" | "ok" | "down" | "models-unknown";

async function checkOmni(): Promise<OmniState> {
  // V6.3.4 D4.1: perform the health check through the Rust core
  // (check_omnirt_health command) — the webview's own fetch is blocked by
  // CORS (mesh IPs send no ACAO headers), which made the dot show
  // "Gateway unreachable" even when the gateway was fine.
  if (typeof (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ === "undefined") {
    // Browser fallback (dev outside Tauri): direct fetch may still work in
    // some environments, keep old behavior.
    try {
      const r = await fetch("http://100.64.0.3:8000/v1/models", {
        headers: { Authorization: "Bearer arkham-cockpit" },
      });
      return r.ok ? "ok" : "down";
    } catch {
      return "down";
    }
  }
  try {
    const health = await invoke<{ ok: boolean; status_code: number; models: number; error?: string }>(
      "check_omnirt_health",
    );
    if (health.ok) return health.models > 0 ? "ok" : "models-unknown";
    return "down";
  } catch {
    return "down";
  }
}

export default function App() {
  const [omni, setOmni] = useState<OmniState>("checking");
  const [inTauri, setInTauri] = useState(false);

  useEffect(() => {
    checkOmni().then(setOmni);
    // IPC-bridge probe: same static UI runs in a browser without Tauri
    setInTauri(
      typeof (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ !==
        "undefined",
    );
  }, []);

  const dot =
    omni === "ok"
      ? "bg-emerald-500"
      : omni === "down"
        ? "bg-arkham-accent"
        : "bg-amber-400";

  const openTwenty = async () => {
    if (!inTauri) {
      window.open("http://100.64.0.4:3020", "_blank");
      return;
    }
    try {
      await invoke("plugin:window|show", { label: "twenty" });
    } catch {
      // capability-guarded; fall back to shell open
      window.open("http://100.64.0.4:3020", "_blank");
    }
  };

  return (
    <div className="min-h-screen bg-arkham-bg p-5 text-arkham-text">
      <header className="mb-5 flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-wide">ARKHAM COCKPIT</h1>
        <span className="rounded bg-arkham-panel px-2 py-0.5 text-[10px] text-arkham-dim">
          TAURI v2
        </span>
      </header>

      <section className="mb-4 rounded-lg border border-arkham-edge bg-arkham-panel p-4">
        <div className="mb-1 flex items-center gap-2">
          <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
          <span className="text-sm font-medium">OmniRouter .3:8000</span>
        </div>
        <p className="text-xs text-arkham-dim">
          {omni === "ok"
            ? "Mesh LLM gateway reachable — catalog loaded"
            : omni === "models-unknown"
              ? "Gateway reachable — catalog parse deferred"
              : omni === "down"
              ? "Gateway unreachable from this host"
              : "Probing mesh gateway…"}
        </p>
      </section>

      <section className="mb-4 rounded-lg border border-arkham-edge bg-arkham-panel p-4">
        <div className="text-sm font-medium">Twenty CRM · Node .4:3020</div>
        <p className="mt-1 text-xs text-arkham-dim">
          Native multi-window shell — no browser tab overhead. Tray: Show /
          Hide / Quit.
        </p>
        <button
          onClick={openTwenty}
          className="mt-3 w-full rounded-md bg-arkham-accent px-3 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Open Twenty CRM Window
        </button>
      </section>

      <section className="rounded-lg border border-arkham-edge bg-arkham-panel p-4 text-xs leading-relaxed text-arkham-dim">
        <div className="mb-1 text-sm font-medium text-arkham-text">
          Meeting audio bridge
        </div>
        WASAPI loopback capture (Windows build) + iOS Shortcut ingest → Genos
        :8645 → Whisper/Groq BYOK → GLM-5.3 Flash synthesis. HMAC token
        enforced on every POST /meeting/ingest.
      </section>
    </div>
  );
}
