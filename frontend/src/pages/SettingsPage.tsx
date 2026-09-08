import { useClerk, useUser } from "@clerk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import AppShell from "../components/AppShell";
import { useApiClient } from "../lib/api";
import "./SettingsPage.css";

const CLASSES = ["6", "7", "8", "9", "10", "11", "12"];

export default function SettingsPage() {
  const { user } = useUser();
  const { signOut } = useClerk();
  const api = useApiClient();
  const queryClient = useQueryClient();

  const { data: profile } = useQuery({ queryKey: ["profile"], queryFn: api.getProfile });
  const { data: usage } = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });

  const updateClass = useMutation({
    mutationFn: (classId: string) => api.updateProfile(classId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile"] }),
  });

  const initials = (user?.fullName ?? user?.username ?? "?")
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <AppShell active="settings">
      <div className="settings-title">Profile & settings</div>
      <div className="settings-card">
        <div className="settings-profile-row">
          <div className="settings-avatar">
            {user?.imageUrl ? <img src={user.imageUrl} alt="" /> : initials}
          </div>
          <div style={{ flex: 1 }}>
            <div className="settings-name">{user?.fullName ?? user?.username}</div>
            <div className="settings-email">{user?.primaryEmailAddress?.emailAddress}</div>
          </div>
        </div>

        <div className="settings-body">
          <div>
            <div className="settings-section-label">DEFAULT CLASS</div>
            <div className="settings-section-hint">Pre-fills every new quiz. You can still pick another class at the time.</div>
            <div className="class-picker">
              {CLASSES.map((c) => (
                <button
                  key={c}
                  className={`class-chip ${profile?.default_class === c ? "selected" : ""}`}
                  onClick={() => updateClass.mutate(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>

          <div className="settings-list">
            <div className="settings-list-row">
              <div>
                <div style={{ fontWeight: 500, fontSize: 14 }}>Email</div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 3 }}>
                  {user?.primaryEmailAddress?.emailAddress}
                </div>
              </div>
            </div>
            <div className="settings-list-row">
              <div>
                <div style={{ fontWeight: 500, fontSize: 14 }}>Daily quiz limit</div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 3 }}>
                  {usage ? `${usage.used_today} of ${usage.limit} used today · resets at midnight` : "Loading…"}
                </div>
              </div>
              {usage && (
                <div style={{ width: 88, height: 6, background: "var(--color-chip-alt)", borderRadius: 3 }}>
                  <div
                    style={{
                      width: `${Math.min(100, (usage.used_today / usage.limit) * 100)}%`,
                      height: 6,
                      background: "var(--color-success)",
                      borderRadius: 3,
                    }}
                  />
                </div>
              )}
            </div>
          </div>

          <div className="settings-footer">
            <span />
            <button onClick={() => signOut()} style={{ color: "var(--color-text-soft)" }}>
              Log out
            </button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
