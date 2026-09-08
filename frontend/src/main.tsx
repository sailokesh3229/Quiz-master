import { ClerkProvider } from "@clerk/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.tsx";
import "./index.css";

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

const queryClient = new QueryClient();

const root = createRoot(document.getElementById("root")!);

if (!CLERK_PUBLISHABLE_KEY) {
  root.render(
    <div style={{ fontFamily: "sans-serif", padding: 40, maxWidth: 560 }}>
      <h1>Missing Clerk configuration</h1>
      <p>
        Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in <code>frontend/.env</code> (copy it from your Clerk dashboard
        → API Keys) and restart the dev server.
      </p>
    </div>,
  );
} else {
  root.render(
    <StrictMode>
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl="/sign-in">
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </ClerkProvider>
    </StrictMode>,
  );
}
