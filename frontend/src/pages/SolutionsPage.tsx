import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useApiClient } from "../lib/api";
import type { MatchingAnswer, SolutionEntry, UserAnswer } from "../lib/types";
import "./SolutionsPage.css";

type Filter = "all" | "right" | "wrong";

function OptionsReview({ entry }: { entry: SolutionEntry }) {
  const options = entry.correct_payload.options ?? [];
  const correctIndex = entry.correct_payload.correct_option_index;
  const userIndex = entry.user_answer as number;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {options.map((opt, i) => {
        const isCorrect = i === correctIndex;
        const isUser = i === userIndex;
        const cls = isCorrect ? "correct-answer" : isUser ? "wrong-answer" : "";
        return (
          <div key={i} className={`solutions-option-row ${cls}`}>
            <span>{opt}</span>
            {isUser && <span style={{ fontSize: 11 }}>your answer{isCorrect ? " · correct" : ""}</span>}
            {!isUser && isCorrect && <span style={{ fontSize: 11 }}>correct answer</span>}
          </div>
        );
      })}
    </div>
  );
}

function FillInBlankReview({ entry }: { entry: SolutionEntry }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <div className={`solutions-option-row ${entry.is_correct ? "correct-answer" : "wrong-answer"}`}>
        <span>Your answer: {String(entry.user_answer)}</span>
      </div>
      {!entry.is_correct && (
        <div className="solutions-option-row correct-answer">
          <span>Correct answer: {entry.correct_payload.answer}</span>
        </div>
      )}
    </div>
  );
}

function MatchingReview({ entry }: { entry: SolutionEntry }) {
  const pairs = entry.correct_payload.pairs ?? {};
  const userPairs = entry.user_answer as MatchingAnswer;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {Object.entries(pairs).map(([left, right], i) => {
        const correct = entry.row_results?.[i] ?? userPairs[left] === right;
        return (
          <div key={left} className={`solutions-option-row ${correct ? "correct-answer" : "wrong-answer"}`}>
            <span>
              {left} → {userPairs[left] ?? "(no answer)"}
            </span>
            {!correct && <span style={{ fontSize: 11 }}>correct: {right}</span>}
          </div>
        );
      })}
    </div>
  );
}

export default function SolutionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { attemptId } = useParams();
  const api = useApiClient();
  const answersFromState = (location.state as { answers?: Record<string, UserAnswer> } | null)?.answers;

  const { data, isLoading, error } = useQuery({
    queryKey: ["solutions", attemptId, answersFromState],
    queryFn: () =>
      attemptId ? api.getSolutionsByAttempt(attemptId) : api.getSolutionsFromAnswers(answersFromState ?? {}),
    enabled: !!attemptId || !!answersFromState,
  });

  const [filter, setFilter] = useState<Filter>("all");
  const [index, setIndex] = useState(0);

  if (!attemptId && !answersFromState) {
    navigate("/");
    return null;
  }
  if (isLoading) return <div className="solutions-shell">Loading…</div>;
  if (error || !data) return <div className="solutions-shell">Couldn't load solutions.</div>;

  const solutions = data.solutions;
  const correctCount = solutions.filter((s) => s.is_correct).length;
  const filtered = solutions.filter((s) => (filter === "all" ? true : filter === "right" ? s.is_correct : !s.is_correct));
  const entry = filtered[Math.min(index, filtered.length - 1)] ?? solutions[0];
  const globalIndex = solutions.indexOf(entry);

  return (
    <div className="solutions-shell">
      <div className="solutions-card">
        <div className="solutions-sidebar">
          <div className="solutions-sidebar-title">
            {correctCount} / {solutions.length} correct
          </div>
          <div className="solutions-sidebar-sub">{entry.topic}</div>
          <div className="solutions-filter-row">
            <button className={`solutions-filter ${filter === "all" ? "active" : ""}`} onClick={() => { setFilter("all"); setIndex(0); }}>
              All {solutions.length}
            </button>
            <button className={`solutions-filter ${filter === "right" ? "active" : ""}`} onClick={() => { setFilter("right"); setIndex(0); }}>
              Right {correctCount}
            </button>
            <button className={`solutions-filter ${filter === "wrong" ? "active" : ""}`} onClick={() => { setFilter("wrong"); setIndex(0); }}>
              Wrong {solutions.length - correctCount}
            </button>
          </div>
          <div className="solutions-list">
            {filtered.map((s, i) => (
              <button
                key={s.question_id}
                className={`solutions-list-item ${i === index ? "current" : ""}`}
                onClick={() => setIndex(i)}
              >
                <span className="solutions-dot" style={{ background: s.is_correct ? "var(--color-success)" : "var(--color-accent-wrong)" }} />
                <span>
                  Q{solutions.indexOf(s) + 1} · {s.topic}
                </span>
              </button>
            ))}
          </div>
          <button className="solutions-back-btn" onClick={() => navigate(-1)}>
            Back to results
          </button>
        </div>

        <div className="solutions-main">
          <div className="q-eyebrow-row">
            <span className="q-eyebrow">
              QUESTION {globalIndex + 1} OF {solutions.length} · {entry.question_type === "MCQ" ? "MCQ" : entry.question_type === "fill_in_blank" ? "FILL IN BLANK" : "MATCHING"}
            </span>
            <span className={`solutions-verdict ${entry.is_correct ? "correct" : "wrong"}`}>
              {entry.is_correct ? "CORRECT" : "INCORRECT"}
            </span>
          </div>
          <div className="q-text" style={{ fontSize: 23, marginBottom: 0 }}>{entry.text}</div>

          {entry.question_type === "MCQ" && <OptionsReview entry={entry} />}
          {entry.question_type === "fill_in_blank" && <FillInBlankReview entry={entry} />}
          {entry.question_type === "matching" && <MatchingReview entry={entry} />}

          <div className="solutions-explanation-box">
            <div className="solutions-explanation-label">SOLUTION</div>
            <div className="solutions-explanation-text">{entry.explanation}</div>
          </div>

          <div className="solutions-nav">
            <button disabled={index === 0} onClick={() => setIndex((i) => i - 1)} style={{ color: index === 0 ? "var(--color-text-disabled)" : "var(--color-text-soft)" }}>
              ← Previous
            </button>
            <button
              disabled={index >= filtered.length - 1}
              onClick={() => setIndex((i) => i + 1)}
              style={{ color: index >= filtered.length - 1 ? "var(--color-text-disabled)" : "var(--color-success)" }}
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
