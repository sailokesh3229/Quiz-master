// Mirrors backend/app/api_schemas.py — keep in sync by hand (no shared
// schema generator in v1; the API surface is small and stable enough that
// a codegen step isn't worth the setup cost yet).

export type QuestionType = "MCQ" | "fill_in_blank" | "matching";
export type Difficulty = "easy" | "medium" | "hard";
export type ScopeType = "single_topic" | "multi_topic" | "all_topics_in_chapter" | "all_chapters_in_subject";

export interface Profile {
  user_id: string;
  default_class: string | null;
}

export interface Chapter {
  chapter: string;
  chapter_number: string | null;
}

export interface Topic {
  topic: string;
  topic_number: string | null;
}

export interface DailyUsage {
  used_today: number;
  limit: number;
}

export interface MCQPublicPayload {
  options: string[];
}

export interface MatchingPublicPayload {
  left_items: string[];
  right_items: string[];
}

export type PublicPayload = MCQPublicPayload | MatchingPublicPayload | Record<string, never>;

export interface DeliveredQuestion {
  question_id: string;
  question_type: QuestionType;
  topic: string;
  chapter: string;
  difficulty: Difficulty;
  text: string;
  payload: PublicPayload;
}

export interface CreateQuizRequestBody {
  class: string;
  subject: string;
  scope_type: ScopeType;
  chapters: string[];
  topics?: string[] | null;
  difficulty: Difficulty;
  question_count: number;
  question_types: QuestionType[];
  allow_partial?: boolean;
}

export interface CreateQuizResponse {
  questions: DeliveredQuestion[];
  requested_count: number;
  delivered_count: number;
  partial: boolean;
}

// The answer shape a user submits per question type.
export type MCQAnswer = number; // option index
export type FillInBlankAnswer = string;
export type MatchingAnswer = Record<string, string>; // left item -> right item
export type UserAnswer = MCQAnswer | FillInBlankAnswer | MatchingAnswer;

export interface SubmitQuizRequestBody {
  class: string;
  subject: string;
  scope: { scope_type: ScopeType; sub_units: string[] };
  difficulty: Difficulty;
  is_requiz: boolean;
  answers: Record<string, UserAnswer>;
}

export interface PerQuestionResult {
  question_id: string;
  question_type: QuestionType;
  topic: string;
  is_correct: boolean;
  row_results?: boolean[] | null;
}

export interface SubmitQuizResponse {
  attempt_id: string | null;
  score_correct: number;
  score_total: number;
  options: string[];
  per_question: PerQuestionResult[];
}

export interface CorrectPayload {
  options?: string[];
  correct_option_index?: number;
  answer?: string;
  left_items?: string[];
  right_items?: string[];
  pairs?: Record<string, string>;
}

export interface SolutionEntry {
  question_id: string;
  question_type: QuestionType;
  topic: string;
  text: string;
  correct_payload: CorrectPayload;
  user_answer: UserAnswer;
  is_correct: boolean;
  row_results?: boolean[] | null;
  explanation: string;
}

export interface SolutionsResponse {
  solutions: SolutionEntry[];
}

export interface AccuracyBucket {
  label: string;
  correct: number;
  total: number;
  accuracy: number | null;
}

export interface WeeklyAttempts {
  week_start: string;
  correct: number;
  total: number;
  attempt_count: number;
}

export interface RecentAttempt {
  attempt_id: string;
  class: string;
  subject: string;
  scope: { scope_type: ScopeType; sub_units: string[] };
  difficulty: Difficulty;
  score_correct: number;
  score_total: number;
  created_at: string;
}

export interface Dashboard {
  overall: AccuracyBucket;
  by_subject: AccuracyBucket[];
  by_chapter: AccuracyBucket[];
  by_question_type: AccuracyBucket[];
  by_difficulty: AccuracyBucket[];
  attempts_over_time: WeeklyAttempts[];
  recent_attempts: RecentAttempt[];
}

export interface ApiErrorBody {
  detail: string;
}
