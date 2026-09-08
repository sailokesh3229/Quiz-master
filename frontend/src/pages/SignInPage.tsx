import { SignIn } from "@clerk/react";
import { clerkAppearance } from "../lib/clerkAppearance";
import "./AuthLayout.css";

export default function SignInPage() {
  return (
    <div className="auth-page">
      <div>
        <div className="auth-brand">
          <div className="auth-brand-title">Quiz Master</div>
          <div className="auth-brand-subtitle">Practice questions straight from your textbook.</div>
        </div>
        <SignIn signUpUrl="/sign-up" appearance={clerkAppearance} />
      </div>
    </div>
  );
}
