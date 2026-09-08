import { useAuth } from "@clerk/react";
import { useMemo } from "react";
import type {
  Chapter,
  CreateQuizRequestBody,
  CreateQuizResponse,
  Dashboard,
  DailyUsage,
  Profile,
  SolutionsResponse,
  SubmitQuizRequestBody,
  SubmitQuizResponse,
  Topic,
  UserAnswer,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  getToken: () => Promise<string | null>,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON — fall back to statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** All backend calls, bound to the current Clerk session token. Call
 * inside a component/hook — never module-level (the token is per-session). */
export function useApiClient() {
  const { getToken } = useAuth();

  return useMemo(() => {
    const call = <T>(path: string, init?: RequestInit) => request<T>(getToken, path, init);

    return {
      getProfile: () => call<Profile>("/api/profile"),
      updateProfile: (defaultClass: string) =>
        call<Profile>("/api/profile", { method: "PUT", body: JSON.stringify({ default_class: defaultClass }) }),

      getSubjects: (classId: string) => call<string[]>(`/api/catalog/subjects?class_=${encodeURIComponent(classId)}`),
      getChapters: (classId: string, subject: string) =>
        call<Chapter[]>(`/api/catalog/chapters?class_=${encodeURIComponent(classId)}&subject=${encodeURIComponent(subject)}`),
      getTopics: (classId: string, subject: string, chapter: string) =>
        call<Topic[]>(
          `/api/catalog/topics?class_=${encodeURIComponent(classId)}&subject=${encodeURIComponent(subject)}&chapter=${encodeURIComponent(chapter)}`,
        ),
      getQuestionCountOptions: (subUnitCount: number, coverageRequired: boolean) =>
        call<{ options: number[] }>(
          `/api/catalog/question-count-options?sub_unit_count=${subUnitCount}&coverage_required=${coverageRequired}`,
        ),

      getUsage: () => call<DailyUsage>("/api/quiz/usage"),
      createQuiz: (body: CreateQuizRequestBody) =>
        call<CreateQuizResponse>("/api/quiz/create", { method: "POST", body: JSON.stringify(body) }),
      submitQuiz: (body: SubmitQuizRequestBody) =>
        call<SubmitQuizResponse>("/api/quiz/submit", { method: "POST", body: JSON.stringify(body) }),
      getSolutionsByAttempt: (attemptId: string) => call<SolutionsResponse>(`/api/quiz/solutions/${attemptId}`),
      getSolutionsFromAnswers: (answers: Record<string, UserAnswer>) =>
        call<SolutionsResponse>("/api/quiz/solutions", { method: "POST", body: JSON.stringify({ answers }) }),

      getDashboard: () => call<Dashboard>("/api/performance/dashboard"),
    };
  }, [getToken]);
}
