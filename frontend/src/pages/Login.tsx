// Login entry point (Security Spec v1.4 §6). In the cloud, "Sign in" redirects to
// the backend BFF /api/v1/auth/login, which begins the Entra ID OIDC flow.
// Locally (VITE_AUTH_ENABLED=false) the session is established automatically via
// LOCAL_AUTH_BYPASS, so this screen is normally not shown; it appears only if the
// session could not be resolved.

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Typography from "@mui/material/Typography";

import { useAuth } from "../auth/AuthContext";

export function Login(): JSX.Element {
  const { login } = useAuth();
  const authEnabled = import.meta.env.VITE_AUTH_ENABLED === "true";

  return (
    <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
      <Paper sx={{ p: 4, maxWidth: 420, textAlign: "center" }} elevation={3}>
        <Typography variant="h5" gutterBottom>
          OPEX Budgeting System
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          {authEnabled
            ? "Sign in with your corporate account to continue."
            : "Your local session could not be established. Confirm the API is running and your LOCAL_AUTH_USER_ID exists in obs.users."}
        </Typography>
        {authEnabled && (
          <Button variant="contained" onClick={login} size="large">
            Sign in with Microsoft
          </Button>
        )}
      </Paper>
    </Box>
  );
}
