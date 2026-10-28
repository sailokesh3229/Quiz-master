import { UserButton } from "@clerk/react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useApiClient } from "../lib/api";
import "./AppShell.css";

interface AppShellProps {
  active: "home" | "progress" | "settings";
  children: ReactNode;
}

export default function AppShell({ active, children }: AppShellProps) {
  const navigate = useNavigate();
  const api = useApiClient();
  const { data: usage } = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });

  return (
    <div className="app-shell">
      <nav className="app-sidebar">
        <div className="app-sidebar-brand-row">
          <div className="app-sidebar-brand-wrap">
            <span className="app-sidebar-mark" aria-hidden="true">Q<span>✦</span></span>
            <div>
              <div className="app-sidebar-brand">Quiz Master</div>
              <div className="app-sidebar-brand-sub">Your learning space</div>
            </div>
          </div>
          <UserButton />
        </div>
        <div className="app-sidebar-label">WORKSPACE</div>
        <button className={`app-sidebar-link ${active === "home" ? "active" : ""}`} aria-current={active === "home" ? "page" : undefined} onClick={() => navigate("/")}>
          <span className="app-nav-icon" aria-hidden="true">⌂</span> Home
        </button>
        <button
          className={`app-sidebar-link ${active === "progress" ? "active" : ""}`}
          aria-current={active === "progress" ? "page" : undefined}
          onClick={() => navigate("/progress")}
        >
          <span className="app-nav-icon" aria-hidden="true">◫</span> My progress
        </button>
        <button
          className={`app-sidebar-link ${active === "settings" ? "active" : ""}`}
          aria-current={active === "settings" ? "page" : undefined}
          onClick={() => navigate("/settings")}
        >
          <span className="app-nav-icon" aria-hidden="true">⚙</span> Settings
        </button>
        <div className="app-sidebar-spacer" />
        {usage && (
          <div className="app-sidebar-usage">
            <div className="app-sidebar-usage-icon" aria-hidden="true">✦</div>
            You've used <b>{usage.used_today} of {usage.limit}</b> quiz generations today.
          </div>
        )}
      </nav>
      <main className="app-main">{children}</main>
    </div>
  );
}
