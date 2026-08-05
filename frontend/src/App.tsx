import { useEffect, useState } from "react";

type HealthResponse = {
  status: "ready" | "not_ready";
  application_status: string;
  detail: string;
  updated_at: string;
};

type ConnectionState =
  | { kind: "loading" }
  | { kind: "online"; health: HealthResponse }
  | { kind: "offline"; detail: string };

async function loadHealth(): Promise<HealthResponse> {
  const response = await fetch("/health/ready", {
    headers: { Accept: "application/json" },
  });
  const body = (await response.json()) as HealthResponse;

  if (!response.ok) {
    throw new Error(body.detail || "서버가 아직 준비되지 않았습니다.");
  }
  return body;
}

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ kind: "loading" });

  useEffect(() => {
    let active = true;

    const refresh = async () => {
      try {
        const health = await loadHealth();
        if (active) setConnection({ kind: "online", health });
      } catch (error) {
        const detail = error instanceof Error ? error.message : "서버 상태를 확인하지 못했습니다.";
        if (active) setConnection({ kind: "offline", detail });
      }
    };

    void refresh();
    const timer = window.setInterval(refresh, 5_000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const statusLabel =
    connection.kind === "online"
      ? "서버 연결됨"
      : connection.kind === "offline"
        ? "서버 연결 안 됨"
        : "서버 확인 중";

  return (
    <main className="page-shell">
      <section className="dashboard-card" aria-labelledby="dashboard-title">
        <p className="eyebrow">SMART DESK</p>
        <h1 id="dashboard-title">대시보드 기본 골조</h1>
        <p className="description">
          React 대시보드와 FastAPI가 같은 애플리케이션 경계에서 동작합니다.
        </p>

        <div className={`status status--${connection.kind}`} role="status">
          <span className="status__dot" aria-hidden="true" />
          <div>
            <strong>{statusLabel}</strong>
            <p>
              {connection.kind === "online"
                ? connection.health.detail
                : connection.kind === "offline"
                  ? connection.detail
                  : "FastAPI readiness를 조회하고 있습니다."}
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
