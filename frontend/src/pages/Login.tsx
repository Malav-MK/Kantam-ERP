import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, get } from "../api";
import { useAuth } from "../auth";
import KantamLogo from "../components/KantamLogo";

type Step = "checking" | "enrol" | "password" | "mfa_setup" | "mfa";

export default function Login() {
  const { signedIn } = useAuth();
  const [step, setStep] = useState<Step>("checking");
  const [device, setDevice] = useState<{ name: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaToken, setMfaToken] = useState("");
  const [otp, setOtp] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);

  useEffect(() => {
    get("/api/auth/device")
      .then((d) => {
        setDevice(d.device);
        setStep(d.enrolled || !d.required ? "password" : "enrol");
      })
      .catch((e: ApiError) => {
        setError(e.code === "zero_trust_denied" ? "This network is not approved for Kantam ERP. Connect to the factory network." : e.message);
        setStep("password");
      });
  }, []);

  const run = (fn: () => Promise<void>) => async (ev: FormEvent) => {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const enrol = run(async () => {
    const r = await api("POST", "/api/auth/device/enroll", { code });
    setDevice(r.device);
    setStep("password");
  });

  const login = run(async () => {
    const r = await api("POST", "/api/auth/login", { username, password });
    setPassword("");
    if (r.mfa_required) {
      setMfaToken(r.mfa_token);
      if (r.mfa_setup_required) {
        setSetup(await api("POST", "/api/auth/mfa/setup", { mfa_token: r.mfa_token }));
        setStep("mfa_setup");
      } else setStep("mfa");
      return;
    }
    await signedIn(r.access_token);
  });

  const verify = run(async () => {
    const r = await api("POST", "/api/auth/mfa/verify", { mfa_token: mfaToken, code: otp });
    await signedIn(r.access_token);
  });

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-brand">
          <KantamLogo className="login-logo" />
          <div className="muted small">ERP + CRM · Bareja plant</div>
        </div>

        {step === "checking" && <p className="muted">Checking this device…</p>}

        {step === "enrol" && (
          <form onSubmit={enrol} className="stack">
            <h2>Enrol this device</h2>
            <p className="muted small">Every PC, tablet and gate device is enrolled once by IT before anyone can sign in on it. Enter the one-time code shown to the administrator.</p>
            <label className="field"><span>Enrolment code</span>
              <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="XXXX-XXXX-XXXX" autoFocus required /></label>
            <button className="btn primary" disabled={busy}>Enrol device</button>
          </form>
        )}

        {step === "password" && (
          <form onSubmit={login} className="stack">
            <h2>Sign in</h2>
            {device && <p className="muted small">Device: <b>{device.name}</b></p>}
            <label className="field"><span>Username</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required /></label>
            <label className="field"><span>Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
            <button className="btn primary" disabled={busy}>Sign in</button>
          </form>
        )}

        {step === "mfa_setup" && setup && (
          <form onSubmit={verify} className="stack">
            <h2>Set up two-step verification</h2>
            <p className="muted small">Your role requires an authenticator app (Google Authenticator, Microsoft Authenticator…). Add this key, then enter the 6-digit code.</p>
            <code className="secret">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
            <details className="small"><summary>Setup link</summary><code className="wrap">{setup.otpauth_uri}</code></details>
            <label className="field"><span>6-digit code</span>
              <input inputMode="numeric" value={otp} onChange={(e) => setOtp(e.target.value)} maxLength={6} autoFocus required /></label>
            <button className="btn primary" disabled={busy}>Verify and continue</button>
          </form>
        )}

        {step === "mfa" && (
          <form onSubmit={verify} className="stack">
            <h2>Two-step verification</h2>
            <label className="field"><span>Code from your authenticator app</span>
              <input inputMode="numeric" value={otp} onChange={(e) => setOtp(e.target.value)} maxLength={6} autoFocus required /></label>
            <button className="btn primary" disabled={busy}>Verify</button>
          </form>
        )}

        {error && <div className="alert danger" role="alert">{error}</div>}
        <p className="muted tiny">Factory-network access only. Activity is recorded against your named account.</p>
      </div>
    </div>
  );
}
