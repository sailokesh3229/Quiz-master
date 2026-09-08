import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import AppShell from "../components/AppShell";
import { useApiClient } from "../lib/api";
import "./ProgressPage.css";

function pctColor(pct: number): string {
  if (pct >= 70) return "var(--color-success)";
  if (pct >= 50) return "#a79a6e";
  return "var(--color-accent)";
}

export default function ProgressPage() {
  const api = useApiClient();
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: api.getDashboard });

  if (!data) {
    return (
      <AppShell active="progress">
        <div className="progress-title">My progress</div>
      </AppShell>
    );
  }

  const overallPct = data.overall.accuracy != null ? Math.round(data.overall.accuracy * 100) : 0;
  const bestSubject = [...data.by_subject].sort((a, b) => (b.accuracy ?? 0) - (a.accuracy ?? 0))[0];
  const totalAttempts = data.recent_attempts.length;

  const weeklyChartData = data.attempts_over_time.map((w) => ({
    week: w.week_start.slice(5),
    accuracy: w.total > 0 ? Math.round((w.correct / w.total) * 100) : 0,
  }));

  return (
    <AppShell active="progress">
      <div className="progress-header">
        <div>
          <div className="progress-title">
            You're at {overallPct}% across {data.overall.total} questions
          </div>
          <div className="progress-sub">Derived from every attempt you've submitted (requiz replays don't count).</div>
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Questions answered</div>
          <div className="stat-value">{data.overall.total}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best subject</div>
          <div className="stat-value" style={{ fontSize: 20 }}>
            {bestSubject ? `${bestSubject.label} · ${Math.round((bestSubject.accuracy ?? 0) * 100)}%` : "—"}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Recent attempts</div>
          <div className="stat-value">{totalAttempts}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Hard-mode accuracy</div>
          <div className="stat-value" style={{ color: pctColor(Math.round(((data.by_difficulty.find((d) => d.label === "hard")?.accuracy) ?? 0) * 100)) }}>
            {data.by_difficulty.find((d) => d.label === "hard")
              ? `${Math.round((data.by_difficulty.find((d) => d.label === "hard")!.accuracy ?? 0) * 100)}%`
              : "—"}
          </div>
        </div>
      </div>

      <div className="panel-grid">
        <div className="panel">
          <div className="panel-label">ACCURACY BY CHAPTER</div>
          {data.by_chapter.length === 0 && <div style={{ fontSize: 13, color: "var(--color-text-muted)" }}>No attempts yet.</div>}
          {data.by_chapter.map((b) => {
            const pct = b.accuracy != null ? Math.round(b.accuracy * 100) : 0;
            const lowSample = b.total < 3;
            return (
              <div className="bar-row" key={b.label} style={{ opacity: lowSample ? 0.55 : 1 }}>
                <div className="bar-row-top">
                  <span>{b.label}</span>
                  <span style={{ fontWeight: 600, color: lowSample ? undefined : pctColor(pct) }}>
                    {lowSample ? `only ${b.total} questions` : `${pct}%`}
                  </span>
                </div>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${pct}%`, background: pctColor(pct) }} />
                </div>
              </div>
            );
          })}
          <div style={{ fontSize: 11, color: "var(--color-text-faint)", marginTop: 8 }}>
            Chapters with fewer than 3 answered questions aren't scored yet.
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="panel">
            <div className="panel-label">BY QUESTION TYPE</div>
            {data.by_question_type.map((b) => {
              const pct = b.accuracy != null ? Math.round(b.accuracy * 100) : 0;
              return (
                <div className="type-row" key={b.label}>
                  <span className="type-row-label">{b.label}</span>
                  <div className="bar-track" style={{ flex: 1 }}>
                    <div className="bar-fill" style={{ width: `${pct}%`, background: pctColor(pct) }} />
                  </div>
                  <span className="type-row-pct" style={{ color: pctColor(pct) }}>
                    {pct}%
                  </span>
                </div>
              );
            })}
          </div>

          <div className="panel" style={{ flex: 1 }}>
            <div className="panel-label">ATTEMPTS PER WEEK</div>
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={weeklyChartData}>
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: "var(--color-text-faint)" }} axisLine={false} tickLine={false} />
                <Tooltip />
                <Bar dataKey="accuracy" fill="var(--color-success)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
