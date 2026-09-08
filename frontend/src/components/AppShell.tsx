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
          <div className="app-sidebar-brand">Quiz Master</div>
          <UserButton />
        </div>
        <button className={`app-sidebar-link ${active === "home" ? "active" : ""}`} onClick={() => navigate("/")}>
          Home
        </button>
        <button
          className={`app-sidebar-link ${active === "progress" ? "active" : ""}`}
          onClick={() => navigate("/progress")}
        >
          My progress
        </button>
        <button
          className={`app-sidebar-link ${active === "settings" ? "active" : ""}`}
          onClick={() => navigate("/settings")}
        >
          Settings
        </button>
        <div className="app-sidebar-spacer" />
        {usage && (
          <div className="app-sidebar-usage">
            You've used <b>{usage.used_today} of {usage.limit}</b> quiz generations today.
          </div>
        )}
      </nav>
      <main className="app-main">{children}</main>
    </div>
  );
}
