import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApiClient } from "../lib/api";
import type {
  CreateQuizRequestBody,
  DeliveredQuestion,
  Difficulty,
  ScopeType,
  SubmitQuizResponse,
  UserAnswer,
} from "../lib/types";
import "./ResultsPage.css";

interface QuizContext {
  class: string;
  subject: string;
  scopeType: ScopeType;
  subUnits: string[];
  difficulty: Difficulty;
  originalRequest?: CreateQuizRequestBody;
}

interface ResultsState {
  result: SubmitQuizResponse;
  questions: DeliveredQuestion[];
  answers: Record<string, UserAnswer>;
  quizContext: QuizContext;
}

function ringColor(pct: number): string {
  if (pct >= 70) return "var(--color-success)";
  if (pct >= 50) return "#a79a6e";
  return "var(--color-accent)";
}

export default function ResultsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const api = useApiClient();
  const state = location.state as ResultsState | null;
  const [loadingNewQuiz, setLoadingNewQuiz] = useState(false);

  if (!state) {
    navigate("/");
    return null;
  }

  const { result, questions, answers, quizContext } = state;
  const pct = result.score_total > 0 ? Math.round((result.score_correct / result.score_total) * 100) : 0;
  const color = ringColor(pct);

  async function startNewQuizSameTopic() {
    if (!quizContext.originalRequest) {
      navigate("/quiz/new", { state: { classId: quizContext.class, subject: quizContext.subject } });
      return;
    }
    setLoadingNewQuiz(true);
    try {
      const fresh = await api.createQuiz(quizContext.originalRequest);
      navigate("/quiz/take", {
        state: {
          questions: fresh.questions,
          partial: fresh.partial,
          originalRequest: quizContext.originalRequest,
          class: quizContext.class,
          subject: quizContext.subject,
          scopeType: quizContext.scopeType,
          subUnits: [...new Set(fresh.questions.map((q) => q.topic))],
          difficulty: quizContext.difficulty,
        },
      });
    } finally {
      setLoadingNewQuiz(false);
    }
  }

  function requizSameQuestions() {
    navigate("/quiz/take", {
      state: {
        questions,
        partial: false,
        originalRequest: quizContext.originalRequest,
        class: quizContext.class,
        subject: quizContext.subject,
        scopeType: quizContext.scopeType,
        subUnits: quizContext.subUnits,
        difficulty: quizContext.difficulty,
        isRequiz: true,
      },
    });
  }

  function seeSolutions() {
    if (result.attempt_id) {
      navigate(`/quiz/solutions/${result.attempt_id}`);
    } else {
      navigate("/quiz/solutions", { state: { answers } });
    }
  }

  return (
    <div className="results-shell">
      <div className="results-card">
        <div className="results-score-row">
          <div className="results-ring" style={{ background: `conic-gradient(${color} ${pct}%, var(--color-divider) 0)` }}>
            <div className="results-ring-inner">{pct}%</div>
          </div>
          <div>
            <div className="results-headline">
              {result.score_correct} of {result.score_total} correct
            </div>
            <div className="results-meta">
              {quizContext.subUnits.join(", ")} · Class {quizContext.class} {quizContext.subject} · {quizContext.difficulty}
            </div>
          </div>
        </div>

        <button className="results-cta" onClick={seeSolutions}>
          See solutions
        </button>

        <div className="results-options">
          <button className="results-option" onClick={startNewQuizSameTopic} disabled={loadingNewQuiz}>
            <div>
              <div className="results-option-title">Start a new quiz on this topic</div>
              <div className="results-option-sub">Fresh questions, same scope</div>
            </div>
            <span>{loadingNewQuiz ? "…" : "→"}</span>
          </button>
          <button className="results-option" onClick={requizSameQuestions}>
            <div>
              <div className="results-option-title">Requiz the same questions</div>
              <div className="results-option-sub">Review only — not recorded</div>
            </div>
            <span>→</span>
          </button>
          <button className="results-option" onClick={() => navigate("/")}>
            <div className="results-option-title">Go home</div>
            <span>→</span>
          </button>
        </div>
      </div>
    </div>
  );
}
