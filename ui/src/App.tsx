import { AttendanceCard } from "./components/AttendanceCard";
import { AttendanceReportDashboard } from "./components/AttendanceReportDashboard";
import { useEffect, useState } from "react";
import "./App.css";
import { getHealth } from "./api";
import { Header } from "./components/Header";
import { VoiceAssistant } from "./components/VoiceAssistant";
import type { HealthResponse } from "./types";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [connected, setConnected] = useState<boolean>(true);
  const [currentView, setCurrentView] = useState<"assistant" | "report">("assistant");

  useEffect(() => {
    let mounted = true;
    const checkHealth = () => {
      getHealth()
        .then((res) => {
          if (mounted) {
            setHealth(res);
            setConnected(true);
          }
        })
        .catch(() => {
          if (mounted) {
            setConnected(false);
          }
        });
    };

    checkHealth();
    const interval = window.setInterval(checkHealth, 10000);

    return () => {
      mounted = false;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <div className="app">
      <Header
        health={health}
        connected={connected}
        activeView={currentView}
        onToggleView={(view) => setCurrentView(view)}
      />

      {!connected && (
        <div className="banner banner-error">
          Cannot reach the Kelvin6K API. Start it with <code>python run_api.py</code>.
        </div>
      )}

      {currentView === "report" ? (
        <main className="report-dashboard-container">
          <AttendanceReportDashboard onClose={() => setCurrentView("assistant")} />
        </main>
      ) : (
        <main className="grid-focused">
          <div className="focused-chat-column">
            <VoiceAssistant focusedMode={true} />
          </div>
          <div className="focused-attendance-column">
            <AttendanceCard onOpenReport={() => setCurrentView("report")} />
          </div>
        </main>
      )}
    </div>
  );
}
