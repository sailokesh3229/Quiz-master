import { SignUp } from "@clerk/react";
import { clerkAppearance } from "../lib/clerkAppearance";
import "./AuthLayout.css";

export default function SignUpPage() {
  return (
    <div className="auth-page">
      <div>
        <div className="auth-brand">
          <div className="auth-brand-title">Quiz Master</div>
          <div className="auth-brand-subtitle">Practice questions straight from your textbook.</div>
        </div>
        <SignUp signInUrl="/sign-in" appearance={clerkAppearance} />
      </div>
    </div>
  );
}
