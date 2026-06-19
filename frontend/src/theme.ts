import { createTheme } from "@mui/material/styles";

// Base MUI theme for the OBS SPA (UX Spec v1.5 — MUI v5, no third-party UI libs).
export const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#1565c0" },
  },
  shape: { borderRadius: 6 },
});
