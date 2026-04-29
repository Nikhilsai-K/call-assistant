/**
 * VocalFlow embeddable voice-agent widget.
 *
 * Customer puts this on their site:
 *
 *   <script src="https://cdn.vocalflow.app/widget.js"
 *           data-agent-id="..."
 *           data-api-base="https://api.vocalflow.app"
 *           defer></script>
 *
 * The widget renders a floating button bottom-right; on click it opens a panel,
 * requests a one-shot LiveKit token from POST /v1/widget/connect/:agent_id, and
 * connects.  Microphone permission is asked only after the user clicks "talk".
 */

import { Room, RoomEvent, Track } from "livekit-client";

interface ConnectResponse {
  room: string;
  livekit_url: string;
  token: string;
  agent_name: string;
  expires_in: number;
}

const STYLE = `
  .vf-btn{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:30px;
    background:#4f46e5;color:#fff;border:none;font-size:24px;cursor:pointer;
    box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:2147483646;transition:transform .15s}
  .vf-btn:hover{transform:scale(1.06)}
  .vf-panel{position:fixed;bottom:96px;right:24px;width:300px;background:#fff;border-radius:16px;
    box-shadow:0 16px 48px rgba(0,0,0,.2);z-index:2147483646;font-family:system-ui,sans-serif;
    color:#111;display:none}
  .vf-panel.open{display:block}
  .vf-head{padding:14px 16px;border-bottom:1px solid #eee;font-weight:600}
  .vf-body{padding:18px 16px;font-size:14px}
  .vf-status{margin-top:8px;color:#6b7280;font-size:12px;min-height:1.2em}
  .vf-cta{margin-top:14px;width:100%;padding:10px;border:none;border-radius:8px;
    background:#4f46e5;color:#fff;font-weight:600;cursor:pointer}
  .vf-cta.end{background:#dc2626}
  .vf-cta:disabled{opacity:.6;cursor:not-allowed}
  .vf-mic-pulse{display:inline-block;width:10px;height:10px;border-radius:50%;
    background:#10b981;margin-right:6px;animation:vfp 1.2s infinite}
  @keyframes vfp{0%{transform:scale(.8);opacity:.7}50%{transform:scale(1.2);opacity:1}100%{transform:scale(.8);opacity:.7}}
`;

class VocalFlowWidget {
  private agentId: string;
  private apiBase: string;
  private button!: HTMLButtonElement;
  private panel!: HTMLDivElement;
  private statusEl!: HTMLDivElement;
  private cta!: HTMLButtonElement;
  private head!: HTMLDivElement;
  private room: Room | null = null;
  private connected = false;

  constructor(agentId: string, apiBase: string) {
    this.agentId = agentId;
    this.apiBase = apiBase.replace(/\/$/, "");
    this.injectStyle();
    this.render();
  }

  private injectStyle() {
    if (document.getElementById("vf-widget-style")) return;
    const s = document.createElement("style");
    s.id = "vf-widget-style";
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  private render() {
    this.button = document.createElement("button");
    this.button.className = "vf-btn";
    this.button.title = "Talk to us";
    this.button.innerHTML = "🎙";
    this.button.onclick = () => this.togglePanel();
    document.body.appendChild(this.button);

    this.panel = document.createElement("div");
    this.panel.className = "vf-panel";

    this.head = document.createElement("div");
    this.head.className = "vf-head";
    this.head.textContent = "Talk to us";

    const body = document.createElement("div");
    body.className = "vf-body";
    body.innerHTML =
      "<div>Click <b>Start call</b> and start speaking. We pick up right away.</div>";
    this.statusEl = document.createElement("div");
    this.statusEl.className = "vf-status";
    body.appendChild(this.statusEl);

    this.cta = document.createElement("button");
    this.cta.className = "vf-cta";
    this.cta.textContent = "Start call";
    this.cta.onclick = () => (this.connected ? this.endCall() : this.startCall());
    body.appendChild(this.cta);

    this.panel.appendChild(this.head);
    this.panel.appendChild(body);
    document.body.appendChild(this.panel);
  }

  private togglePanel() {
    this.panel.classList.toggle("open");
  }

  private setStatus(text: string) {
    this.statusEl.textContent = text;
  }

  private async startCall() {
    this.cta.disabled = true;
    this.setStatus("Connecting…");
    try {
      const resp = await fetch(`${this.apiBase}/v1/widget/connect/${this.agentId}`, {
        method: "POST",
      });
      if (resp.status === 429) throw new Error("rate_limited");
      if (!resp.ok) throw new Error(`connect_failed_${resp.status}`);
      const data = (await resp.json()) as ConnectResponse;
      this.head.textContent = data.agent_name;

      const room = new Room({ adaptiveStream: true, dynacast: true });
      this.room = room;

      room.on(RoomEvent.Disconnected, () => this.onDisconnected());
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach();
          el.setAttribute("style", "display:none");
          document.body.appendChild(el);
        }
      });

      await room.connect(data.livekit_url, data.token);
      await room.localParticipant.setMicrophoneEnabled(true);

      this.connected = true;
      this.cta.textContent = "End call";
      this.cta.classList.add("end");
      this.cta.disabled = false;
      this.setStatus("Live — go ahead and talk.");
    } catch (e) {
      this.setStatus(
        e instanceof Error && e.message === "rate_limited"
          ? "Too many attempts. Try again in a minute."
          : "Couldn't connect. Please try again.",
      );
      this.cta.disabled = false;
    }
  }

  private async endCall() {
    if (this.room) await this.room.disconnect();
    this.onDisconnected();
  }

  private onDisconnected() {
    this.connected = false;
    this.room = null;
    this.cta.textContent = "Start call";
    this.cta.classList.remove("end");
    this.cta.disabled = false;
    this.setStatus("Call ended.");
  }
}

(function bootstrap() {
  const script = document.currentScript as HTMLScriptElement | null;
  if (!script) return;
  const agentId = script.dataset.agentId;
  const apiBase = script.dataset.apiBase;
  if (!agentId) {
    console.warn("[VocalFlow] data-agent-id missing on script tag");
    return;
  }
  if (!apiBase) {
    console.warn("[VocalFlow] data-api-base missing on script tag");
    return;
  }
  // Defer until DOM ready.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => new VocalFlowWidget(agentId, apiBase));
  } else {
    new VocalFlowWidget(agentId, apiBase);
  }
})();
