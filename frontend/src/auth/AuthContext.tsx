// Authentication context (Security Spec v1.4 §6; Local Dev Spec v1.1 §6.2).
//
// BFF model: the backend owns the OIDC code exchange and the httpOnly refresh
// cookie; the SPA never talks to Entra directly. The access token is held in
// memory only (in api/client.ts), never in localStorage.
//
//  - VITE_AUTH_ENABLED=false (local): skip OIDC entirely. Resolve the session by
//    calling GET /api/v1/me, which the backend serves via LOCAL_AUTH_BYPASS.
//  - VITE_AUTH_ENABLED=true (cloud): on load, POST /api/v1/auth/refresh to mint an
//    access token from the refresh cookie, then load /me. login()/logout() redirect
//    through the backend BFF endpoints.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getMe } from "../api/users";
import { redirectToLogin, setAccessToken } from "../api/client";
import type { Me } from "../types";

const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === "true";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthState {
  status: AuthStatus;
  user: Me | null;
  login: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap(): Promise<void> {
      try {
        if (AUTH_ENABLED) {
          const resp = await fetch("/api/v1/auth/refresh", { method: "POST" });
          if (resp.ok) {
            const body = (await resp.json()) as { access_token?: string };
            setAccessToken(body.access_token ?? null);
          }
        }
        const me = await getMe();
        if (!cancelled) {
          setUser(me);
          setStatus("authenticated");
        }
      } catch {
        if (!cancelled) {
          setUser(null);
          setStatus("unauthenticated");
        }
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(() => {
    redirectToLogin();
  }, []);

  const logout = useCallback(() => {
    async function doLogout(): Promise<void> {
      try {
        const resp = await fetch("/api/v1/auth/logout", { method: "POST" });
        const body = (await resp.json().catch(() => ({}))) as { logout_url?: string };
        setAccessToken(null);
        setUser(null);
        setStatus("unauthenticated");
        if (AUTH_ENABLED && body.logout_url) window.location.href = body.logout_url;
      } catch {
        setAccessToken(null);
        setUser(null);
        setStatus("unauthenticated");
      }
    }
    void doLogout();
  }, []);

  const value = useMemo<AuthState>(
    () => ({ status, user, login, logout }),
    [status, user, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- hook co-located with its provider
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
