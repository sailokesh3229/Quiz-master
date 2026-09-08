import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import FillInBlankQuestion from "../components/questions/FillInBlankQuestion";
import MatchingQuestion from "../components/questions/MatchingQuestion";
import MCQQuestion from "../components/questions/MCQQuestion";
import { useApiClient } from "../lib/api";
import type {
  CreateQuizRequestBody,
  DeliveredQuestion,
  Difficulty,
  MatchingAnswer,
  ScopeType,
  UserAnswer,
} from "../lib/types";
import "./QuizTakingPage.css";

interface TakeQuizState {
  questions: DeliveredQuestion[];
  partial: boolean;
  originalRequest?: CreateQuizRequestBody;
  class: string;
  subject: string;
  scopeType: ScopeType;
  subUnits: string[];
  difficulty: Difficulty;
  isRequiz?: boolean;
}

const TYPE_LABEL: Record<string, string> = {
  MCQ: "MULTIPLE CHOICE",
  fill_in_blank: "FILL IN THE BLANK",
  matching: "MATCHING",
};

function isAnswered(answer: UserAnswer | undefined, type: string): boolean {
  if (answer === undefined) return false;
  if (type === "matching") return Object.keys(answer as MatchingAnswer).length > 0;
  if (type === "fill_in_blank") return String(answer).trim().length > 0;
  return true;
}

export default function QuizTakingPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const api = useApiClient();
  const state = location.state as TakeQuizState | null;

  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, UserAnswer>>({});
  const [submitting, setSubmitting] = useState(false);

  if (!state) {
    navigate("/");
    return null;
  }

  const { questions } = state;
  const question = questions[index];
  const answeredCount = questions.filter((q) => isAnswered(answers[q.question_id], q.question_type)).length;

  function setAnswer(value: UserAnswer) {
    setAnswers((a) => ({ ...a, [question.question_id]: value }));
  }

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const result = await api.submitQuiz({
        class: state!.class,
        subject: state!.subject,
        scope: { scope_type: state!.scopeType, sub_units: state!.subUnits },
        difficulty: state!.difficulty,
        is_requiz: state!.isRequiz ?? false,
        answers,
      });
      navigate("/quiz/results", {
        state: {
          result,
          questions,
          answers,
          quizContext: {
            class: state!.class,
            subject: state!.subject,
            scopeType: state!.scopeType,
            subUnits: state!.subUnits,
            difficulty: state!.difficulty,
            originalRequest: state!.originalRequest,
          },
        },
      });
    } finally {
      setSubmitting(false);
    }
  }

  const isLast = index === questions.length - 1;

  return (
    <div className="take-shell">
      <div className="take-card">
        <div className="take-header">
          <span style={{ fontWeight: 500 }}>{question.chapter}</span>
          <span className="take-header-meta">
            Class {state.class} · {state.subject} · {state.difficulty}
          </span>
          <div className="take-header-spacer" />
          <button className="take-leave" onClick={() => navigate("/")}>
            Leave
          </button>
        </div>

        <div className="take-body">
          <div className="q-eyebrow-row">
            <span className="q-eyebrow">
              QUESTION {index + 1} OF {questions.length}
            </span>
            <span className="q-type-chip">{TYPE_LABEL[question.question_type]}</span>
          </div>

          {question.question_type === "MCQ" && (
            <MCQQuestion
              question={question}
              value={answers[question.question_id] as number | undefined}
              onChange={setAnswer}
            />
          )}
          {question.question_type === "fill_in_blank" && (
            <FillInBlankQuestion
              question={question}
              value={answers[question.question_id] as string | undefined}
              onChange={setAnswer}
            />
          )}
          {question.question_type === "matching" && (
            <MatchingQuestion
              question={question}
              value={answers[question.question_id] as MatchingAnswer | undefined}
              onChange={setAnswer}
            />
          )}
        </div>

        <div className="take-footer">
          <button className="take-prev" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>
            ← Previous
          </button>
          <div className="take-pills">
            {questions.map((q, i) => (
              <button
                key={q.question_id}
                className={`take-pill ${i === index ? "current" : isAnswered(answers[q.question_id], q.question_type) ? "answered" : ""}`}
                onClick={() => setIndex(i)}
              >
                {i + 1}
              </button>
            ))}
          </div>
          {isLast ? (
            <button className="take-next" disabled={submitting || answeredCount === 0} onClick={handleSubmit}>
              {submitting ? "Submitting…" : "Submit"}
            </button>
          ) : (
            <button className="take-next" onClick={() => setIndex((i) => i + 1)}>
              Next →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
