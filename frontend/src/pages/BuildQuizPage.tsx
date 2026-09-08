import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, useApiClient } from "../lib/api";
import type { CreateQuizResponse, Difficulty, QuestionType, ScopeType } from "../lib/types";
import "./BuildQuizPage.css";

const CLASSES = ["6", "7", "8", "9", "10", "11", "12"];
const DIFFICULTIES: { value: Difficulty; label: string }[] = [
  { value: "easy", label: "Easy" },
  { value: "medium", label: "Medium" },
  { value: "hard", label: "Hard" },
];
const QUESTION_TYPES: { value: QuestionType; label: string }[] = [
  { value: "MCQ", label: "Multiple choice" },
  { value: "fill_in_blank", label: "Fill in the blank" },
  { value: "matching", label: "Matching" },
];

type Category = "subject_wide" | "chapter_topic";
type StepId = "class" | "scope" | "chapters" | "topics" | "difficulty" | "length";

interface WizardState {
  classId: string;
  category: Category | null;
  subject: string | null;
  allChapters: boolean;
  selectedChapters: string[];
  allTopics: boolean;
  selectedTopics: string[];
  difficulty: Difficulty | null;
  questionCount: number | null;
  questionTypes: QuestionType[];
}

function stepSequence(s: WizardState): StepId[] {
  const steps: StepId[] = ["class", "scope"];
  if (s.category === "chapter_topic") {
    steps.push("chapters");
    if (!s.allChapters) steps.push("topics");
  }
  steps.push("difficulty", "length");
  return steps;
}

function resolveScopeType(s: WizardState): ScopeType {
  if (s.category === "subject_wide" || s.allChapters) return "all_chapters_in_subject";
  if (s.allTopics) return "all_topics_in_chapter";
  return s.selectedTopics.length <= 1 ? "single_topic" : "multi_topic";
}

type FlowState =
  | { kind: "wizard" }
  | { kind: "generating" }
  | { kind: "failed"; message: string; canPartial: boolean }
  | { kind: "daily_limit" };

export default function BuildQuizPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const api = useApiClient();
  const initialClass = (location.state as { classId?: string; subject?: string } | null)?.classId ?? "10";
  const initialSubject = (location.state as { subject?: string } | null)?.subject ?? null;

  const [stepIndex, setStepIndex] = useState(initialSubject ? 1 : 0);
  const [state, setState] = useState<WizardState>({
    classId: initialClass,
    category: initialSubject ? "chapter_topic" : null,
    subject: initialSubject,
    allChapters: false,
    selectedChapters: [],
    allTopics: false,
    selectedTopics: [],
    difficulty: null,
    questionCount: null,
    questionTypes: [],
  });
  const [flow, setFlow] = useState<FlowState>({ kind: "wizard" });

  const steps = stepSequence(state);
  const currentStep = steps[stepIndex];

  const { data: subjects } = useQuery({
    queryKey: ["subjects", state.classId],
    queryFn: () => api.getSubjects(state.classId),
    enabled: currentStep === "scope",
  });

  const { data: chapters } = useQuery({
    queryKey: ["chapters", state.classId, state.subject],
    queryFn: () => api.getChapters(state.classId, state.subject!),
    enabled: !!state.subject && (currentStep === "chapters" || currentStep === "length"),
  });

  const { data: topicsByChapter } = useQuery({
    queryKey: ["topics", state.classId, state.subject, state.selectedChapters],
    queryFn: async () => {
      const entries = await Promise.all(
        state.selectedChapters.map(async (ch) => [ch, await api.getTopics(state.classId, state.subject!, ch)] as const),
      );
      return Object.fromEntries(entries);
    },
    enabled: currentStep === "topics" && state.selectedChapters.length > 0,
  });

  const coverageInfo = useMemo(() => {
    const scopeType = resolveScopeType(state);
    if (scopeType === "all_chapters_in_subject") {
      return { coverageRequired: true, subUnitCount: chapters?.length ?? 0 };
    }
    if (scopeType === "all_topics_in_chapter") {
      const total = topicsByChapter
        ? Object.values(topicsByChapter).reduce((sum, arr) => sum + arr.length, 0)
        : 0;
      return { coverageRequired: true, subUnitCount: total };
    }
    return { coverageRequired: false, subUnitCount: 0 };
  }, [state, chapters, topicsByChapter]);

  const { data: countOptions } = useQuery({
    queryKey: ["question-count-options", coverageInfo.coverageRequired, coverageInfo.subUnitCount],
    queryFn: () => api.getQuestionCountOptions(coverageInfo.subUnitCount, coverageInfo.coverageRequired),
    enabled: currentStep === "length",
  });

  function goNext() {
    setStepIndex((i) => Math.min(i + 1, steps.length - 1));
  }
  function goBack() {
    setStepIndex((i) => Math.max(i - 1, 0));
  }

  function toggleInArray(arr: string[], value: string): string[] {
    return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
  }

  async function submit(allowPartial: boolean) {
    setFlow({ kind: "generating" });
    const scopeType = resolveScopeType(state);
    const allChapterNames = (chapters ?? []).map((c) => c.chapter);
    const chosenChapters = scopeType === "all_chapters_in_subject" ? allChapterNames : state.selectedChapters;
    const chosenTopics = scopeType === "single_topic" || scopeType === "multi_topic" ? state.selectedTopics : null;

    const originalRequest = {
      class: state.classId,
      subject: state.subject!,
      scope_type: scopeType,
      chapters: chosenChapters,
      topics: chosenTopics,
      difficulty: state.difficulty!,
      question_count: state.questionCount!,
      question_types: state.questionTypes,
    };
    try {
      const result: CreateQuizResponse = await api.createQuiz({ ...originalRequest, allow_partial: allowPartial });
      navigate("/quiz/take", {
        state: {
          questions: result.questions,
          partial: result.partial,
          originalRequest,
          class: state.classId,
          subject: state.subject,
          scopeType,
          subUnits: [...new Set(result.questions.map((q) => q.topic))],
          difficulty: state.difficulty,
        },
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setFlow({ kind: "daily_limit" });
      } else if (e instanceof ApiError && e.status === 503) {
        setFlow({ kind: "failed", message: e.message, canPartial: true });
      } else {
        setFlow({ kind: "failed", message: e instanceof Error ? e.message : "Something went wrong.", canPartial: false });
      }
    }
  }

  if (flow.kind === "generating") {
    return (
      <div className="wizard-shell">
        <div className="wizard-panel">
          <div className="wizard-panel-eyebrow">SETTING YOUR PAPER</div>
          <div className="wizard-panel-title">{state.subject}</div>
          <div className="wizard-panel-sub">
            {state.questionCount} questions · {state.difficulty} · Class {state.classId}
          </div>
          <div className="wizard-progress-track">
            <div className="wizard-progress-fill" />
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 12 }}>Usually about 15 seconds</div>
        </div>
      </div>
    );
  }

  if (flow.kind === "daily_limit") {
    return (
      <div className="wizard-shell">
        <div className="wizard-panel" style={{ borderRadius: 26 }}>
          <div className="wizard-panel-eyebrow">DAILY LIMIT</div>
          <div className="wizard-panel-title">That's your quizzes for today — nicely done.</div>
          <div className="wizard-panel-sub">
            Fresh question writing resets at midnight. Until then you can still replay anything you've taken.
          </div>
          <button className="wizard-panel-btn primary" style={{ background: "var(--color-text)" }} onClick={() => navigate("/")}>
            Go home
          </button>
          <button className="wizard-panel-btn secondary" onClick={() => navigate("/progress")}>
            See my progress
          </button>
        </div>
      </div>
    );
  }

  if (flow.kind === "failed") {
    return (
      <div className="wizard-shell">
        <div className="wizard-panel">
          <div className="wizard-error-icon">!</div>
          <div className="wizard-panel-title">We couldn't finish building your quiz</div>
          <div className="wizard-panel-sub">
            Question writing is temporarily unavailable — nothing to do with your answers or connection. Your
            selections are saved.
          </div>
          <button className="wizard-panel-btn primary" onClick={() => submit(false)}>
            Try again
          </button>
          {flow.canPartial && (
            <button className="wizard-panel-btn secondary" onClick={() => submit(true)}>
              Take fewer questions from your bank instead
            </button>
          )}
          <button className="wizard-panel-btn ghost" onClick={() => navigate("/")}>
            Go home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard-shell">
      <div className="wizard-card">
        <div className="wizard-rail">
          <div className="wizard-rail-label">YOUR QUIZ</div>
          {railItem("Class", state.classId ? `Class ${state.classId}` : null, currentStep === "class")}
          {railItem(
            "Scope",
            state.category ? (state.category === "subject_wide" ? `Subject-wide · ${state.subject ?? "…"}` : `Chapter quiz · ${state.subject ?? "…"}`) : null,
            currentStep === "scope",
          )}
          {steps.includes("chapters") &&
            railItem(
              "Chapters",
              state.allChapters ? "All chapters" : state.selectedChapters.length ? state.selectedChapters.join(", ") : null,
              currentStep === "chapters",
            )}
          {steps.includes("topics") &&
            railItem(
              "Topics",
              state.allTopics ? "All topics" : state.selectedTopics.length ? `${state.selectedTopics.length} selected` : null,
              currentStep === "topics",
            )}
          {railItem("Difficulty", state.difficulty, currentStep === "difficulty")}
          {railItem(
            "Length & formats",
            state.questionCount ? `${state.questionCount} · ${state.questionTypes.join(", ")}` : null,
            currentStep === "length",
          )}
          <div className="wizard-rail-spacer" />
          <div className="wizard-rail-hint">Tap Back to change an earlier step.</div>
        </div>

        <div className="wizard-body">
          <div className="wizard-step-label">
            STEP {stepIndex + 1} OF {steps.length}
          </div>

          {currentStep === "class" && (
            <>
              <div className="wizard-question">Which class are you practising?</div>
              <div className="wizard-hint">You can always pick a different one later.</div>
              <div className="wizard-options">
                {CLASSES.map((c) => (
                  <button
                    key={c}
                    className={`wizard-radio-row ${state.classId === c ? "selected" : ""}`}
                    onClick={() => setState((s) => ({ ...s, classId: c }))}
                  >
                    Class {c}
                  </button>
                ))}
              </div>
            </>
          )}

          {currentStep === "scope" && (
            <>
              <div className="wizard-question">What kind of quiz?</div>
              <div className="wizard-hint">Choose a whole subject, or a specific chapter/topic.</div>
              <button
                className={`wizard-radio-row ${state.category === "subject_wide" ? "selected" : ""}`}
                onClick={() => setState((s) => ({ ...s, category: "subject_wide" }))}
              >
                Subject-wide quiz
              </button>
              <button
                className={`wizard-radio-row ${state.category === "chapter_topic" ? "selected" : ""}`}
                onClick={() => setState((s) => ({ ...s, category: "chapter_topic" }))}
              >
                Chapter / topic quiz
              </button>
              <div className="wizard-hint" style={{ marginTop: 6 }}>Subject</div>
              <div className="wizard-options">
                {(subjects ?? []).map((subj) => (
                  <button
                    key={subj}
                    className={`wizard-option-row ${state.subject === subj ? "selected" : ""}`}
                    onClick={() => setState((s) => ({ ...s, subject: subj }))}
                  >
                    {subj}
                  </button>
                ))}
              </div>
            </>
          )}

          {currentStep === "chapters" && (
            <>
              <div className="wizard-question">Which chapter(s) from {state.subject}?</div>
              <div className="wizard-hint">Pick as many as you like, or take the whole subject.</div>
              <div
                className="wizard-shortcut"
                onClick={() => setState((s) => ({ ...s, allChapters: !s.allChapters, selectedChapters: [] }))}
              >
                <span className={`wizard-checkbox ${state.allChapters ? "checked" : ""}`}>{state.allChapters ? "✓" : ""}</span>
                <div>
                  <div className="wizard-shortcut-title">All chapters in this subject</div>
                  <div className="wizard-shortcut-sub">{chapters?.length ?? "…"} chapters</div>
                </div>
              </div>
              {!state.allChapters && (
                <div className="wizard-options">
                  {(chapters ?? []).map((ch) => (
                    <button
                      key={ch.chapter}
                      className={`wizard-option-row ${state.selectedChapters.includes(ch.chapter) ? "selected" : ""}`}
                      onClick={() =>
                        setState((s) => ({ ...s, selectedChapters: toggleInArray(s.selectedChapters, ch.chapter) }))
                      }
                    >
                      <span className={`wizard-checkbox ${state.selectedChapters.includes(ch.chapter) ? "checked" : ""}`}>
                        {state.selectedChapters.includes(ch.chapter) ? "✓" : ""}
                      </span>
                      {ch.chapter}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          {currentStep === "topics" && (
            <>
              <div className="wizard-question">Which topics?</div>
              <div className="wizard-hint">Pick as many as you like, or take the whole chapter.</div>
              <div
                className="wizard-shortcut"
                onClick={() => setState((s) => ({ ...s, allTopics: !s.allTopics, selectedTopics: [] }))}
              >
                <span className={`wizard-checkbox ${state.allTopics ? "checked" : ""}`}>{state.allTopics ? "✓" : ""}</span>
                <div>
                  <div className="wizard-shortcut-title">All topics in {state.selectedChapters.length > 1 ? "these chapters" : "this chapter"}</div>
                </div>
              </div>
              {!state.allTopics && (
                <div className="wizard-options">
                  {state.selectedChapters.map((ch) => (
                    <div key={ch}>
                      {state.selectedChapters.length > 1 && (
                        <div className="wizard-hint" style={{ margin: "10px 0 4px" }}>{ch}</div>
                      )}
                      {(topicsByChapter?.[ch] ?? []).map((t) => (
                        <button
                          key={t.topic}
                          className={`wizard-option-row ${state.selectedTopics.includes(t.topic) ? "selected" : ""}`}
                          onClick={() => setState((s) => ({ ...s, selectedTopics: toggleInArray(s.selectedTopics, t.topic) }))}
                        >
                          <span className={`wizard-checkbox ${state.selectedTopics.includes(t.topic) ? "checked" : ""}`}>
                            {state.selectedTopics.includes(t.topic) ? "✓" : ""}
                          </span>
                          {t.topic}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {currentStep === "difficulty" && (
            <>
              <div className="wizard-question">Pick a difficulty</div>
              <div className="wizard-hint">One difficulty per quiz.</div>
              {DIFFICULTIES.map((d) => (
                <button
                  key={d.value}
                  className={`wizard-radio-row ${state.difficulty === d.value ? "selected" : ""}`}
                  onClick={() => setState((s) => ({ ...s, difficulty: d.value }))}
                >
                  {d.label}
                </button>
              ))}
            </>
          )}

          {currentStep === "length" && (
            <>
              <div className="wizard-question">How many questions, and what kind?</div>
              <div className="wizard-hint">
                {coverageInfo.coverageRequired
                  ? `Covering ${coverageInfo.subUnitCount} sub-units — the count must be more than that.`
                  : "Pick a length."}
              </div>
              <div className="wizard-count-grid">
                {(countOptions?.options ?? []).map((n) => (
                  <button
                    key={n}
                    className={`wizard-count-chip ${state.questionCount === n ? "selected" : ""}`}
                    onClick={() => setState((s) => ({ ...s, questionCount: n }))}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div className="wizard-hint">Question types (pick at least one)</div>
              <div className="wizard-options" style={{ flex: "none" }}>
                {QUESTION_TYPES.map((qt) => (
                  <button
                    key={qt.value}
                    className={`wizard-option-row ${state.questionTypes.includes(qt.value) ? "selected" : ""}`}
                    onClick={() => setState((s) => ({ ...s, questionTypes: toggleInArray(s.questionTypes, qt.value) as QuestionType[] }))}
                  >
                    <span className={`wizard-checkbox ${state.questionTypes.includes(qt.value) ? "checked" : ""}`}>
                      {state.questionTypes.includes(qt.value) ? "✓" : ""}
                    </span>
                    {qt.label}
                  </button>
                ))}
              </div>
            </>
          )}

          <div className="wizard-footer">
            <button className="wizard-back" onClick={stepIndex === 0 ? () => navigate("/") : goBack}>
              ← Back
            </button>
            {currentStep === "length" ? (
              <button
                className="wizard-continue"
                disabled={!state.questionCount || state.questionTypes.length === 0}
                onClick={() => submit(false)}
              >
                Build my quiz
              </button>
            ) : (
              <button className="wizard-continue" disabled={!canContinue(state, currentStep)} onClick={goNext}>
                Continue
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function canContinue(s: WizardState, step: StepId): boolean {
  switch (step) {
    case "class":
      return !!s.classId;
    case "scope":
      return !!s.category && !!s.subject;
    case "chapters":
      return s.allChapters || s.selectedChapters.length > 0;
    case "topics":
      return s.allTopics || s.selectedTopics.length > 0;
    case "difficulty":
      return !!s.difficulty;
    default:
      return true;
  }
}

function railItem(label: string, value: string | null, isCurrent: boolean) {
  const done = value !== null;
  return (
    <div key={label} className={`wizard-rail-item ${isCurrent ? "current" : ""}`}>
      <span className={`wizard-rail-dot ${done ? "done" : "pending"}`}>{done ? "✓" : ""}</span>
      <div>
        <div className="wizard-rail-item-label">{label}</div>
        <div className="wizard-rail-item-value">{value ?? "—"}</div>
      </div>
    </div>
  );
}
