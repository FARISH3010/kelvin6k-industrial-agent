/** Application header: operational branding, server health, and status chips. */

import "./Header.css";
import type { HealthResponse } from "../types";

interface Props {
  health: HealthResponse | null;
  connected: boolean;
  activeView?: "assistant" | "report";
  onToggleView?: (view: "assistant" | "report") => void;
}

function StatusTile({
  label,
  value,
  tone,
  detail,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "bad" | "muted";
  detail?: string;
}) {
  return (
    <div className={`status-tile status-tile-${tone}`} title={detail}>
      <span className="status-tile-label">{label}</span>
      <span className="status-tile-value">
        <span className={`status-dot status-${tone === "ok" ? "live" : tone === "bad" ? "bad" : "ok"}`} />
        {value}
      </span>
    </div>
  );
}

export function Header({ health, connected, activeView = "assistant", onToggleView }: Props) {
  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <span className="brand-mark-ring" />
          K6
        </div>
        <div>
          <h1>KELVIN6K ROBOT AI</h1>
          <span className="brand-sub">Operational Intelligence &amp; Attendance System</span>
        </div>
      </div>

      {onToggleView && (
        <div className="header-view-toggle">
          <button
            type="button"
            className={`header-view-btn ${activeView === "assistant" ? "header-view-btn-active" : ""}`}
            onClick={() => onToggleView("assistant")}
          >
            🎙️ Assistant Floor
          </button>
          <button
            type="button"
            className={`header-view-btn ${activeView === "report" ? "header-view-btn-active" : ""}`}
            onClick={() => onToggleView("report")}
          >
            📊 Attendance Report
          </button>
        </div>
      )}

      <div className="status-tiles">
        <StatusTile
          label="Backend API"
          value={connected ? "CONNECTED" : "OFFLINE"}
          tone={connected ? "ok" : "bad"}
          detail={connected ? "FastAPI server running and responsive" : "Cannot reach backend server"}
        />
      </div>

      <div className="header-chips">
        {health ? (
          <>
            <span className="header-chip" title="Active Question Answering Engine">
              LLM: {health.llm}
            </span>
            <span className="header-chip" title="Speech to Text Engine">
              STT: {health.stt}
            </span>
            <span className="header-chip" title="Troubleshooting Knowledge Retrieval">
              RAG: {health.rag_chunks} chunks
            </span>
          </>
        ) : null}
        <span className="header-chip header-chip-readonly">READ-ONLY</span>
      </div>
    </header>
  );
}
