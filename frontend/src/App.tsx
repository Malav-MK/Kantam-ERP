import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import { useProfileTheme } from "./theme";
import { AuditLog, GateBoard, MachineBoard, MyWork, OwnerDashboard, PlanningBoard, Shortages, StoresDaily, Trace, TvBoard } from "./pages/Boards";
import { ResourceDetail, ResourceList } from "./pages/ResourcePages";
import { ApprovalDetail, ApprovalsInbox } from "./pages/Approvals";
import { ForcedPasswordChange, RequirePerm } from "./components/Guard";

function Routed() {
  const { profile, loading } = useAuth();
  useProfileTheme();
  if (loading) return <div className="splash">Loading Kantam ERP…</div>;
  if (!profile) return <Login />;
  if (profile.must_change_password) return <ForcedPasswordChange />;
  const tvOnly = profile.levels.length === 1 && profile.levels[0] === "viewer";
  if (tvOnly) return <TvBoard />;
  return (
    <Routes>
      <Route path="/tv" element={<RequirePerm any={["dashboard.tv.view"]}><TvBoard /></RequirePerm>} />
      <Route element={<Layout />}>
        <Route index element={<MyWork />} />
        <Route path="dashboard" element={<RequirePerm any={["dashboard.owner.view"]}><OwnerDashboard /></RequirePerm>} />
        <Route path="machines" element={<RequirePerm any={["dashboard.owner.view", "dashboard.department.view"]}><MachineBoard /></RequirePerm>} />
        <Route path="planning-board" element={<RequirePerm any={["planning.planline.view"]}><PlanningBoard /></RequirePerm>} />
        <Route path="stores" element={<RequirePerm any={["inventory.stockmove.view"]}><StoresDaily /></RequirePerm>} />
        <Route path="shortages" element={<RequirePerm any={["purchase.requisition.view", "dashboard.owner.view", "planning.planline.view"]}><Shortages /></RequirePerm>} />
        <Route path="gate" element={<RequirePerm any={["dashboard.gate.view"]}><GateBoard /></RequirePerm>} />
        <Route path="trace" element={<RequirePerm any={["inventory.lot.view"]}><Trace /></RequirePerm>} />
        <Route path="audit" element={<RequirePerm any={["audit.view"]}><AuditLog /></RequirePerm>} />
        <Route path="approvals" element={<RequirePerm any={["core.approvalrequest.view"]}><ApprovalsInbox /></RequirePerm>} />
        <Route path="approvals/:id" element={<RequirePerm any={["core.approvalrequest.view"]}><ApprovalDetail /></RequirePerm>} />
        <Route path="r/core.approvalrequest/:id" element={<RequirePerm any={["core.approvalrequest.view"]}><ApprovalDetail /></RequirePerm>} />
        <Route path="r/:key" element={<ResourceList />} />
        <Route path="r/:key/:id" element={<ResourceDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routed />
      </BrowserRouter>
    </AuthProvider>
  );
}
