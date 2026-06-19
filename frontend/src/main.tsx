import React from "react";
import ReactDOM from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { theme } from "./theme";

async function enableMocking(): Promise<void> {
  // MSW lets the SPA run against a fully mocked API (Local Dev Spec v1.1 §10.3),
  // activated with VITE_API_MOCK=true. Otherwise requests hit the real backend
  // via the Vite proxy.
  if (import.meta.env.VITE_API_MOCK !== "true") return;
  const { worker } = await import("./mocks/browser");
  await worker.start({ onUnhandledRequest: "bypass" });
}

void enableMocking().then(() => {
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </ThemeProvider>
    </React.StrictMode>,
  );
});
