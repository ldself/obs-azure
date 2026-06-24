import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { useAuth } from "./auth/AuthContext";
import { Login } from "./pages/Login";
import { BusinessRulesConfig } from "./pages/admin/BusinessRulesConfig";
import { IngestionMonitor } from "./pages/admin/IngestionMonitor";
import { UserManagement } from "./pages/admin/UserManagement";

function FullPageSpinner(): JSX.Element {
  return (
    <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
      <CircularProgress />
    </Box>
  );
}

export default function App(): JSX.Element {
  const { status } = useAuth();

  if (status === "loading") return <FullPageSpinner />;
  if (status === "unauthenticated") return <Login />;

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/admin/users" replace />} />
        <Route path="/admin/users" element={<UserManagement />} />
        <Route path="/admin/ingestion" element={<IngestionMonitor />} />
        <Route path="/admin/business-rules" element={<BusinessRulesConfig />} />
        <Route path="*" element={<Navigate to="/admin/users" replace />} />
      </Routes>
    </AppShell>
  );
}
