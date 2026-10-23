import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, get } from "../api";
import { useAuth } from "../auth";
import { fmtDateTime } from "../format";
import { ThemeSettings } from "../theme";
import KantamLogo from "./KantamLogo";
import Icon, { IconName, MODULE_ICONS } from "./Icon";

type Note = { id: string; title: string; body: string; severity: string; resource: string; object_id: string; next_action: string; created_at: string; seen_at: string | null };

type NavItem = { to: string; label: string; perm: string[]; icon: IconName };

// Design book p.04: personal work first, then factory flow, then role-scoped support.
const PERSONAL: NavItem[] = [
  { to: "/", label: "My work", perm: [], icon: "home" },
  { to: "/r/tasks.task", label: "Follow-up Centre", perm: ["tasks.task.view"], icon: "flag" },
  { to: "/approvals", label: "Approvals", perm: ["core.approvalrequest.view"], icon: "check" },
  { to: "/r/core.outboxmessage", label: "Notifications / outbox", perm: ["outbox.approve"], icon: "bell" },
];
const FLOW = ["crm", "sales", "planning", "production", "inventory", "purchase", "quality", "fg"];
const SUPPORT = ["gate", "tasks", "masters", "core"];

const BOARDS: NavItem[] = [
  { to: "/dashboard", label: "Owner overview", perm: ["dashboard.owner.view"], icon: "dashboard" },
  { to: "/machines", label: "Machine board", perm: ["dashboard.owner.view", "dashboard.department.view"], icon: "machine" },
  { to: "/planning-board", label: "Planning board", perm: ["planning.planline.view"], icon: "calendar" },
  { to: "/inventory", label: "Stock inventory", perm: ["inventory.stockbalance.view"], icon: "boxes" },
  { to: "/stores", label: "Stores daily", perm: ["inventory.stockmove.view"], icon: "boxes" },
  { to: "/shortages", label: "Shortages", perm: ["purchase.requisition.view", "dashboard.owner.view", "planning.planline.view"], icon: "alert" },
  { to: "/gate", label: "Gate & muster", perm: ["dashboard.gate.view"], icon: "shield" },
  { to: "/trace", label: "Traceability", perm: ["inventory.lot.view"], icon: "trace" },
  { to: "/audit", label: "Audit log", perm: ["audit.view"], icon: "audit" },
  { to: "/tv", label: "TV view", perm: ["dashboard.tv.view"], icon: "tv" },
];

export default function Layout() {
  const { profile, modules, can, logout } = useAuth();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState<Note[]>([]);
  const [unseen, setUnseen] = useState(0);
  const [bell, setBell] = useState(false);
  const [menu, setMenu] = useState(false);
  const [popup, setPopup] = useState<Note | null>(null);
  const [themeOpen, setThemeOpen] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState(0);

  const pollApprovals = useCallback(() => {
    if (!can("core.approvalrequest.view")) return;
    get<{ results: { created_by: string }[] }>("/api/r/core.approvalrequest", { status: "pending", page_size: 100 })
      .then((r) => setPendingApprovals(r.results.filter((a) => a.created_by !== profile?.id).length))
      .catch(() => undefined);
  }, [can, profile]);
  useEffect(() => {
    pollApprovals();
    const t = window.setInterval(pollApprovals, 60_000);
    return () => window.clearInterval(t);
  }, [pollApprovals]);

  const poll = useCallback(() => {
    get<{ unseen: number; items: Note[] }>("/api/notifications").then((r) => {
      setNotes(r.items);
      setUnseen((prev) => {
        const fresh = r.items.find((n) => !n.seen_at && n.severity !== "info");
        if (r.unseen > prev && fresh) setPopup(fresh);
        return r.unseen;
      });
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    poll();
    const t = window.setInterval(poll, 30_000);
    return () => window.clearInterval(t);
  }, [poll]);

  const open = (n: Note) => {
    api("POST", `/api/notifications/${n.id}/seen`).then(poll);
    setBell(false);
    setPopup(null);
    if (n.resource && n.object_id) nav(`/r/${n.resource}/${n.object_id}`);
  };
  const allowed = (b: NavItem) => !b.perm.length || b.perm.some(can);
  const boards = BOARDS.filter(allowed);
  const personal = PERSONAL.filter(allowed);
  const personalPaths = new Set(personal.map((p) => p.to));
  // Child records (SO lines, recipe lines ...) live in their parent's tabs, not in the menu.
  const menuModules = modules
    .filter((m) => m.key !== "dashboard")
    .map((m) => ({ ...m, resources: m.resources.filter((r) => r.nav !== false && !personalPaths.has(`/r/${r.key}`)) }))
    .filter((m) => m.resources.length);
  const activeModule = pathname.startsWith("/r/") ? pathname.slice(3).split(".")[0] : "";
  const isOpen = (key: string) => expanded[key] ?? key === activeModule;
  const section = (title: string, keys: string[]) => {
    const list = menuModules.filter((m) => keys.includes(m.key)).sort((a, b) => keys.indexOf(a.key) - keys.indexOf(b.key));
    if (!list.length) return null;
    return (
      <div className="nav-section">
        <div className="nav-heading">{title}</div>
        {list.map((m) => (
          <div key={m.key} className={`nav-group ${isOpen(m.key) ? "open" : ""}`}>
            <button className="nav-title nav-toggle" aria-expanded={isOpen(m.key)} onClick={() => setExpanded((s) => ({ ...s, [m.key]: !isOpen(m.key) }))}>
              <Icon name={MODULE_ICONS[m.key] ?? "database"} size={14} />{m.label}<span className="nav-caret" aria-hidden>{isOpen(m.key) ? "−" : "+"}</span>
            </button>
            {isOpen(m.key) && m.resources.map((r) => <NavLink key={r.key} to={`/r/${r.key}`}>{r.label}</NavLink>)}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className={`shell ${menu ? "menu-open" : ""}`}>
      <aside className="sidebar" onClick={(e) => (e.target as HTMLElement).closest("a") && setMenu(false)}>
        <div className="side-brand"><KantamLogo className="side-logo" /><div className="tiny side-sub">ERP + CRM · Bareja plant</div></div>
        <nav>
          <div className="nav-section">
            <div className="nav-heading">Personal work</div>
            {personal.map((b) => (
              <NavLink key={b.to} to={b.to} end={b.to === "/"}>
                <Icon name={b.icon} size={16} />{b.label}
                {b.to === "/approvals" && pendingApprovals > 0 && <span className="nav-count" aria-label={`${pendingApprovals} waiting`}>{pendingApprovals}</span>}
              </NavLink>
            ))}
          </div>
          {boards.length > 0 && (
            <div className="nav-section">
              <div className="nav-heading">Boards</div>
              {boards.map((b) => <NavLink key={b.to} to={b.to}><Icon name={b.icon} size={16} />{b.label}</NavLink>)}
            </div>
          )}
          {section("Factory flow", FLOW)}
          {section("Role-scoped support", SUPPORT)}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="icon-btn menu-btn" onClick={() => setMenu((m) => !m)} aria-label="Menu"><Icon name="menu" size={22} /></button>
          <div className="shift-chip" title="Day 09:00-21:00 · Night 21:00-09:00">{profile?.current_shift.label} shift</div>
          <div className="spacer" />
          <div className="bell-wrap">
            <button className="icon-btn bell" onClick={() => setBell((b) => !b)} aria-label={`${unseen} unread notifications`}>
              <Icon name="bell" size={20} />{unseen > 0 && <span className="badge">{unseen > 99 ? "99+" : unseen}</span>}
            </button>
            {bell && (
              <div className="bell-menu">
                <header><b>Notifications</b><button className="link" onClick={() => api("POST", "/api/notifications/seen").then(poll)}>Mark all seen</button></header>
                {notes.length === 0 && <p className="muted small pad">Nothing pending.</p>}
                {notes.map((n) => (
                  <button key={n.id} className={`note ${n.severity} ${n.seen_at ? "seen" : ""}`} onClick={() => open(n)}>
                    <b>{n.title}</b>
                    {n.next_action && <span className="small">Next: {n.next_action}</span>}
                    <span className="tiny muted">{fmtDateTime(n.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="icon-btn" onClick={() => setThemeOpen(true)} aria-label="Theme and colours" title="Theme & colours"><Icon name="palette" size={20} /></button>
          <div className="who">
            <div className="who-name">{profile?.full_name}</div>
            <div className="tiny muted">{profile?.department ?? ""} · {profile?.roles.join(", ")}</div>
          </div>
          <button className="btn small" onClick={() => logout()}><Icon name="logout" size={16} />Sign out</button>
        </header>
        {popup && (
          <div className={`toast ${popup.severity}`} role="status">
            <b>{popup.title}</b>
            {popup.next_action && <span>{popup.next_action}</span>}
            <div className="row gap"><button className="btn small primary" onClick={() => open(popup)}>Open</button><button className="btn small" onClick={() => setPopup(null)}>Dismiss</button></div>
          </div>
        )}
        <main><Outlet /></main>
        {themeOpen && <ThemeSettings onClose={() => setThemeOpen(false)} />}
      </div>
    </div>
  );
}
