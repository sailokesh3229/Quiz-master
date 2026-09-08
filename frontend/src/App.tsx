import { RedirectToSignIn, Show } from "@clerk/react";
import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import BuildQuizPage from "./pages/BuildQuizPage";
import HomePage from "./pages/HomePage";
import ProgressPage from "./pages/ProgressPage";
import QuizTakingPage from "./pages/QuizTakingPage";
import ResultsPage from "./pages/ResultsPage";
import SettingsPage from "./pages/SettingsPage";
import SignInPage from "./pages/SignInPage";
import SignUpPage from "./pages/SignUpPage";
import SolutionsPage from "./pages/SolutionsPage";

function Protected({ children }: { children: ReactElement }) {
  return (
    <>
      <Show when="signed-in">{children}</Show>
      <Show when="signed-out">
        <RedirectToSignIn />
      </Show>
    </>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/sign-in/*" element={<SignInPage />} />
      <Route path="/sign-up/*" element={<SignUpPage />} />

      <Route path="/" element={<Protected><HomePage /></Protected>} />
      <Route path="/quiz/new" element={<Protected><BuildQuizPage /></Protected>} />
      <Route path="/quiz/take" element={<Protected><QuizTakingPage /></Protected>} />
      <Route path="/quiz/results" element={<Protected><ResultsPage /></Protected>} />
      <Route path="/quiz/solutions" element={<Protected><SolutionsPage /></Protected>} />
      <Route path="/quiz/solutions/:attemptId" element={<Protected><SolutionsPage /></Protected>} />
      <Route path="/progress" element={<Protected><ProgressPage /></Protected>} />
      <Route path="/settings" element={<Protected><SettingsPage /></Protected>} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
