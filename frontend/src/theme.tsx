import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import { Modal } from "./components/ui";

export type ThemePrefs = {
  mode?: "light" | "dark" | "system";
  preset?: "kantam" | "book" | "steel" | "blue" | "green" | "custom";
  accent?: string;
  sidebar?: "dark" | "light" | "brand" | "navy";
  density?: "comfortable" | "compact";
  text?: "normal" | "large" | "xlarge";
};

// Kantam red and neutral grey come from the official logo (#E31F26 / #888888).
export const PRESETS: Record<Exclude<ThemePrefs["preset"], undefined>, { label: string; accent: string }> = {
  kantam: { label: "Kantam red", accent: "#e31f26" },
  // Design book (16-Oct-2026) palette: navy navigation, teal actions; red is kept for brand and critical holds.
  book: { label: "Design book (navy & teal)", accent: "#006d77" },
  steel: { label: "Steel grey", accent: "#5b5f66" },
  blue: { label: "Industrial blue", accent: "#1f5fa8" },
  green: { label: "Plant green", accent: "#0f7b5f" },
  custom: { label: "Custom", accent: "#e31f26" },
};

export const DEFAULT_THEME: Required<ThemePrefs> = { mode: "system", preset: "kantam", accent: PRESETS.kantam.accent, sidebar: "dark", density: "comfortable", text: "normal" };
const CACHE_KEY = "kantam.theme";
const HEX = /^#[0-9a-f]{6}$/i;

export function resolveTheme(p?: ThemePrefs | null): Required<ThemePrefs> {
  const t = { ...DEFAULT_THEME, ...(p ?? {}) };
  const accent = t.preset === "custom" && t.accent && HEX.test(t.accent) ? t.accent : PRESETS[t.preset]?.accent ?? DEFAULT_THEME.accent;
  return { ...t, accent };
}

function inkFor(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.45 ? "#111111" : "#ffffff";
}

export function applyTheme(p?: ThemePrefs | null) {
  const t = resolveTheme(p);
  const root = document.documentElement;
  root.dataset.mode = t.mode;
  root.dataset.preset = t.preset;
  root.dataset.sidebar = t.sidebar;
  root.dataset.density = t.density;
  root.dataset.text = t.text;
  root.style.setProperty("--brand", t.accent);
  root.style.setProperty("--brand-ink", inkFor(t.accent));
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(p ?? {}));
  } catch {
    /* storage may be unavailable; theme still applies for this page */
  }
}

export function applyCachedTheme() {
  let cached: ThemePrefs | null = null;
  try {
    cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
  } catch {
    cached = null;
  }
  applyTheme(cached);
}

/** Applies the signed-in user's saved theme whenever the profile changes. */
export function useProfileTheme() {
  const { profile } = useAuth();
  useEffect(() => {
    if (profile) applyTheme((profile as any).ui_preferences ?? {});
  }, [profile]);
}

export function ThemeSettings({ onClose }: { onClose: () => void }) {
  const { profile } = useAuth();
  const [prefs, setPrefs] = useState<ThemePrefs>(() => ({ ...((profile as any)?.ui_preferences ?? {}) }));
  const [saved, setSaved] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const timer = useRef<number>();
  const t = resolveTheme(prefs);

  const update = (patch: ThemePrefs) => {
    const next = { ...prefs, ...patch };
    setPrefs(next);
    applyTheme(next);
    setSaved("saving");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api("PATCH", "/api/auth/preferences", patch)
        .then((r) => {
          if (profile) (profile as any).ui_preferences = r.ui_preferences;
          setSaved("saved");
        })
        .catch(() => setSaved("error"));
    }, 300);
  };

  const reset = () => {
    setPrefs({});
    applyTheme({});
    api("PATCH", "/api/auth/preferences", { reset: true }).then(() => {
      if (profile) (profile as any).ui_preferences = {};
      setSaved("saved");
    }).catch(() => setSaved("error"));
  };

  return (
    <Modal title="Display settings" onClose={onClose}>
      <div className="stack">
        <div>
          <div className="setting-label">Appearance</div>
          <div className="seg">
            {(["light", "dark", "system"] as const).map((m) => (
              <button key={m} className={t.mode === m ? "on" : ""} onClick={() => update({ mode: m })}>
                {m === "system" ? "Match device" : m[0].toUpperCase() + m.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="setting-label">Accent colour</div>
          <div className="swatches">
            {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).filter((k) => k !== "custom").map((k) => (
              <button key={k} className={`swatch ${t.preset === k ? "on" : ""}`} onClick={() => update(k === "book" ? { preset: k, sidebar: "navy" } : { preset: k })} aria-pressed={t.preset === k}>
                <span className="chip" style={{ background: PRESETS[k].accent }} />
                {PRESETS[k].label}
              </button>
            ))}
            <label className={`swatch ${t.preset === "custom" ? "on" : ""}`}>
              <input type="color" value={t.preset === "custom" ? t.accent : prefs.accent ?? "#e31f26"}
                onChange={(e) => update({ preset: "custom", accent: e.target.value })} aria-label="Custom accent colour" />
              Custom
            </label>
          </div>
        </div>

        <div>
          <div className="setting-label">Sidebar</div>
          <div className="seg">
            {(["dark", "navy", "light", "brand"] as const).map((s) => (
              <button key={s} className={t.sidebar === s ? "on" : ""} onClick={() => update({ sidebar: s })}>
                {s === "brand" ? "Accent" : s[0].toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="setting-label">Text size</div>
          <div className="seg">
            {([["normal", "Normal"], ["large", "Large"], ["xlarge", "Extra large"]] as const).map(([k, l]) => (
              <button key={k} className={t.text === k ? "on" : ""} onClick={() => update({ text: k })}>{l}</button>
            ))}
          </div>
        </div>

        <div>
          <div className="setting-label">Density</div>
          <div className="seg">
            <button className={t.density === "comfortable" ? "on" : ""} onClick={() => update({ density: "comfortable" })}>Comfortable (touch)</button>
            <button className={t.density === "compact" ? "on" : ""} onClick={() => update({ density: "compact" })}>Compact (office)</button>
          </div>
        </div>

        <div className="theme-preview">
          <button className="btn primary">Primary action</button>
          <button className="btn">Secondary</button>
          <span className="pill ok">released</span>
          <span className="pill warn">held</span>
          <span className="pill danger">blocked</span>
        </div>

        <div className="row gap">
          <button className="btn" onClick={reset}>Reset to Kantam default</button>
          <span className="spacer" />
          <span className="small muted">
            {saved === "saving" ? "Saving…" : saved === "saved" ? "Saved to your account" : saved === "error" ? "Could not save - applied on this device only" : "Follows you on every enrolled device"}
          </span>
        </div>
      </div>
    </Modal>
  );
}
