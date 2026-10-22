import { LOGO_VIEWBOX, MARK_PATH, RULES, WORDMARK_PATH } from "./logoPaths";

/**
 * Official Kantam Kontainers logo. The grey artwork uses --logo-ink and the two
 * rules use --logo-rule, so the logo stays legible on light, dark and accent backgrounds.
 */
export default function KantamLogo({ className = "", title = "Kantam Kontainers" }: { className?: string; title?: string }) {
  return (
    <svg className={`kantam-logo ${className}`} viewBox={LOGO_VIEWBOX} role="img" aria-label={title} xmlns="http://www.w3.org/2000/svg">
      <title>{title}</title>
      <path d={MARK_PATH} fill="var(--logo-ink)" fillRule="evenodd" clipRule="evenodd" />
      <path d={WORDMARK_PATH} fill="var(--logo-ink)" />
      {RULES.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--logo-rule)" strokeWidth={1.0001} strokeMiterlimit={2.6131} />
      ))}
    </svg>
  );
}
