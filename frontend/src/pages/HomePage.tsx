import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppShell from "../components/AppShell";
import { useApiClient } from "../lib/api";
import "./HomePage.css";

const CLASSES = ["6", "7", "8", "9", "10", "11", "12"];

function accuracyColor(pct: number): string {
  if (pct >= 70) return "var(--color-success)";
  if (pct >= 50) return "#a79a6e";
  return "var(--color-accent)";
}

export default function HomePage() {
  const navigate = useNavigate();
  const api = useApiClient();
  const [classId, setClassId] = useState<string>("10");

  const { data: profile } = useQuery({ queryKey: ["profile"], queryFn: api.getProfile });
  const { data: subjects } = useQuery({
    queryKey: ["subjects", classId],
    queryFn: () => api.getSubjects(classId),
  });
  const { data: dashboard } = useQuery({ queryKey: ["dashboard"], queryFn: api.getDashboard });

  useEffect(() => {
    if (profile?.default_class) setClassId(profile.default_class);
  }, [profile?.default_class]);

  const weakestChapter = dashboard?.by_chapter
    .filter((b) => b.total >= 3 && b.accuracy !== null)
    .sort((a, b) => (a.accuracy ?? 1) - (b.accuracy ?? 1))[0];

  return (
    <AppShell active="home">
      <div className="home-header">
        <div>
          <div className="home-title">Class {classId} · pick a subject</div>
          <div className="home-subtitle">Or practise a different class</div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <select className="home-class-select" value={classId} onChange={(e) => setClassId(e.target.value)}>
            {CLASSES.map((c) => (
              <option key={c} value={c}>
                Class {c}
              </option>
            ))}
          </select>
          <button className="home-start-btn" onClick={() => navigate("/quiz/new", { state: { classId } })}>
            Start a quiz
          </button>
        </div>
      </div>

      <div className="subject-grid">
        {(subjects ?? []).map((subject) => {
          const bucket = dashboard?.by_subject.find((b) => b.label === subject);
          const pct = bucket?.accuracy != null ? Math.round(bucket.accuracy * 100) : null;
          return (
            <button
              key={subject}
              className="subject-card"
              onClick={() => navigate("/quiz/new", { state: { classId, subject } })}
            >
              <div className="subject-card-name">{subject}</div>
              {pct !== null ? (
                <>
                  <div className="subject-card-pct" style={{ color: accuracyColor(pct) }}>
                    {pct}%
                  </div>
                  <div className="subject-card-bar">
                    <div className="subject-card-bar-fill" style={{ width: `${pct}%`, background: accuracyColor(pct) }} />
                  </div>
                  <div className="subject-card-meta">{bucket?.total} questions answered</div>
                </>
              ) : (
                <div className="subject-card-meta">No attempts yet</div>
              )}
            </button>
          );
        })}
      </div>

      {weakestChapter && (
        <div className="nudge-banner">
          Your accuracy on <b>{weakestChapter.label}</b> is {Math.round((weakestChapter.accuracy ?? 0) * 100)}%. Worth
          another look.
        </div>
      )}

      {dashboard && dashboard.recent_attempts.length > 0 && (
        <div className="recent-attempts">
          {dashboard.recent_attempts.slice(0, 5).map((a) => (
            <div className="recent-attempt-row" key={a.attempt_id}>
              <span>
                {a.subject} · {a.scope.sub_units.join(", ")}
              </span>
              <span>
                {a.score_correct}/{a.score_total}
              </span>
            </div>
          ))}
        </div>
      )}
    </AppShell>
  );
}
