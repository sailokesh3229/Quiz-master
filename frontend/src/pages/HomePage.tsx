import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
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
  const [selectedClass, setSelectedClass] = useState<string | null>(null);

  const { data: profile } = useQuery({ queryKey: ["profile"], queryFn: api.getProfile });
  const classId = selectedClass ?? profile?.default_class ?? "10";
  const { data: subjects } = useQuery({
    queryKey: ["subjects", classId],
    queryFn: () => api.getSubjects(classId),
  });
  const { data: dashboard } = useQuery({ queryKey: ["dashboard"], queryFn: api.getDashboard });

  const weakestChapter = dashboard?.by_chapter
    .filter((b) => b.total >= 3 && b.accuracy !== null)
    .sort((a, b) => (a.accuracy ?? 1) - (b.accuracy ?? 1))[0];
  const overallPct = dashboard?.overall.accuracy != null ? Math.round(dashboard.overall.accuracy * 100) : null;

  return (
    <AppShell active="home">
      <div className="home-hero">
        <div className="home-hero-copy">
          <div className="home-eyebrow"><span className="home-eyebrow-dot" /> YOUR STUDY SPACE</div>
          <h1 className="home-title">A little practice.<br /><span>A lot of progress.</span></h1>
          <p className="home-subtitle">Explore your NCERT subjects and build a quiz that fits what you want to learn today.</p>
          <div className="home-hero-actions">
            <label className="home-class-label" htmlFor="home-class-select">Studying in</label>
            <select id="home-class-select" className="home-class-select" value={classId} onChange={(e) => setSelectedClass(e.target.value)}>
              {CLASSES.map((c) => <option key={c} value={c}>Class {c}</option>)}
            </select>
            <button className="home-start-btn" onClick={() => navigate("/quiz/new", { state: { classId } })}>
              Start a quiz <span aria-hidden="true">↗</span>
            </button>
          </div>
        </div>
        <div className="home-hero-visual" aria-label="Your learning overview">
          <div className="home-orbit home-orbit-one" /><div className="home-orbit home-orbit-two" />
          <div className="home-visual-star" aria-hidden="true">✦</div>
          <div className="home-visual-card">
            <span className="home-visual-icon" aria-hidden="true">✎</span>
            <div className="home-visual-label">QUESTIONS ANSWERED</div>
            <div className="home-visual-value">{dashboard?.overall.total ?? "—"}</div>
            <div className="home-visual-foot">Every question is a step forward</div>
          </div>
          {overallPct !== null && <div className="home-visual-badge">↗ {overallPct}% overall accuracy</div>}
        </div>
      </div>

      <div className="home-section-heading">
        <div><div className="home-section-kicker">PICK YOUR PATH</div><h2>Explore subjects</h2></div>
        <span>Class {classId} · {subjects?.length ?? 0} subjects</span>
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
              <div className="subject-card-top"><span className="subject-card-icon" aria-hidden="true">{subject.slice(0, 1).toUpperCase()}</span><span className="subject-card-arrow" aria-hidden="true">↗</span></div>
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
                <div className="subject-card-meta">Start exploring this subject</div>
              )}
              <div className="subject-card-action">Build a quiz <span aria-hidden="true">→</span></div>
            </button>
          );
        })}
      </div>
      {subjects?.length === 0 && <div className="home-empty">No subjects are available for Class {classId} yet.</div>}

      {weakestChapter && (
        <div className="nudge-banner">
          <span className="nudge-icon" aria-hidden="true">✦</span>
          <div><strong>A good place to focus next</strong><div>Your accuracy on <b>{weakestChapter.label}</b> is {Math.round((weakestChapter.accuracy ?? 0) * 100)}%. A little more practice could help.</div></div>
        </div>
      )}

      {dashboard && dashboard.recent_attempts.length > 0 && (
        <section className="home-recent-section">
          <div className="home-section-heading"><div><div className="home-section-kicker">YOUR JOURNEY</div><h2>Recent attempts</h2></div><button className="home-text-link" onClick={() => navigate("/progress")}>View progress →</button></div>
          <div className="recent-attempts">
            {dashboard.recent_attempts.slice(0, 5).map((a) => (
              <div className="recent-attempt-row" key={a.attempt_id}>
                <span><b>{a.subject}</b><small>{a.scope.sub_units.join(", ")}</small></span>
                <span className="recent-attempt-score">{a.score_correct}/{a.score_total}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </AppShell>
  );
}
