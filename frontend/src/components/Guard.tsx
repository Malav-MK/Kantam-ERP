import { FormEvent, ReactNode, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import Icon from "./Icon";
import KantamLogo from "./KantamLogo";
import { ErrorBox } from "./ui";

/**
 * Client-side route guard. This is a usability layer only: every API call is
 * authorised again on the server (deny by default), so hiding or showing a
 * screen here never grants or removes real access.
 */
export function RequirePerm({ any, children }: { any: string[]; children: ReactNode }) {
  const { can } = useAuth();
  if (any.length && !any.some(can)) {
    return (
      <div className="page">
        <div className="empty">
          <Icon name="lock" size={28} />
          <h2>Not available for your role</h2>
          <p className="muted">Ask your administrator if you need access. Requests are checked on the server either way.</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

/** Shown when the account has a temporary password (enforced server-side too). */
export function ForcedPasswordChange() {
  const { logout, signedIn } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== repeat) return setError("The new passwords do not match.");
    setBusy(true);
    setError("");
    try {
      await api("POST", "/api/auth/change-password", { current_password: current, new_password: next });
      const r = await api("POST", "/api/auth/refresh");
      await signedIn(r.access_token);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><KantamLogo className="login-logo" /></div>
        <h2>Set your own password</h2>
        <p className="muted small">Your account has a temporary password from an administrator. Choose a new one (at least 12 characters, not a common password) before continuing.</p>
        <label className="field"><span>Temporary password</span><input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
        <label className="field"><span>New password</span><input type="password" autoComplete="new-password" minLength={12} value={next} onChange={(e) => setNext(e.target.value)} required /></label>
        <label className="field"><span>Repeat new password</span><input type="password" autoComplete="new-password" minLength={12} value={repeat} onChange={(e) => setRepeat(e.target.value)} required /></label>
        <ErrorBox error={error} />
        <button className="btn primary" disabled={busy}>Save and continue</button>
        <button type="button" className="btn" onClick={() => logout()}><Icon name="logout" size={16} />Sign out</button>
      </form>
    </div>
  );
}
