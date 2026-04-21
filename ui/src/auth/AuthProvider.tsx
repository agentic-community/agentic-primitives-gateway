import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { UserManager, type User } from "oidc-client-ts";
import { useNavigate } from "react-router-dom";
import { api, setApiAuthToken } from "../api/client";

interface AuthConfig {
  backend: string;
  issuer?: string;
  client_id?: string;
  scopes?: string;
}

interface AuthContextValue {
  /** The authenticated user (null while loading or if noop auth). */
  user: User | null;
  /** True while the initial auth check is in progress. */
  loading: boolean;
  /** The access token for API calls (empty string if noop). */
  token: string;
  /** Trigger login redirect. */
  login: () => void;
  /** Log out and clear session. */
  logout: () => void;
  /** Auth backend type from server config. */
  backend: string;
  /** True once the whoami check has resolved. False while loading. */
  principalLoaded: boolean;
  /** Whether the authenticated principal has admin scope (from /whoami). */
  isAdmin: boolean;
  /** Server-side principal id (e.g. ``"alice"`` or ``"noop"``) — the
   *  owner_id of agents/teams this user creates. */
  principalId: string;
  /** Groups the principal is a member of (used to match ``shared_with``). */
  principalGroups: string[];
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  token: "",
  login: () => {},
  logout: () => {},
  backend: "noop",
  principalLoaded: false,
  isAdmin: false,
  principalId: "",
  principalGroups: [],
});

export function useAuth() {
  return useContext(AuthContext);
}

/** Build the redirect URI for OIDC callbacks. */
function redirectUri() {
  return `${window.location.origin}/ui/callback`;
}

export default function AuthProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [user, setUserState] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [backend, setBackend] = useState("noop");
  const [isAdmin, setIsAdmin] = useState(false);
  const [principalId, setPrincipalId] = useState("");
  const [principalGroups, setPrincipalGroups] = useState<string[]>([]);
  const [principalLoaded, setPrincipalLoaded] = useState(false);
  const managerRef = useRef<UserManager | null>(null);
  const initRef = useRef(false);
  const navigate = useNavigate();

  // Sync the API token immediately when the user changes, before children re-render.
  const setUser = useCallback((u: User | null) => {
    setApiAuthToken(u?.access_token || "");
    setUserState(u);
  }, []);

  useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;

    (async () => {
      try {
        // Fetch auth config from the gateway
        const res = await fetch("/auth/config");
        const config: AuthConfig = await res.json();
        setBackend(config.backend);

        if (config.backend !== "jwt" || !config.issuer || !config.client_id) {
          // Noop or api_key — no OIDC flow needed
          setLoading(false);
          return;
        }

        const mgr = new UserManager({
          authority: config.issuer,
          client_id: config.client_id,
          redirect_uri: redirectUri(),
          post_logout_redirect_uri: `${window.location.origin}/ui/`,
          scope: config.scopes || "openid profile email",
          response_type: "code",
          automaticSilentRenew: true,
          // Skip the extra userinfo request — claims are already in the token.
          loadUserInfo: false,
          // Short timeout for silent renew (used for token refresh, not initial login).
          silentRequestTimeoutInSeconds: 5,
        });
        managerRef.current = mgr;

        // Handle callback if we're on the callback path
        if (window.location.pathname === "/ui/callback") {
          try {
            const callbackUser = await mgr.signinRedirectCallback();
            setUser(callbackUser);
            const returnTo =
              sessionStorage.getItem("auth_return_to") || "/";
            sessionStorage.removeItem("auth_return_to");
            // Strip the /ui basename — React Router navigate is relative to it
            const path = returnTo.replace(/^\/ui/, "") || "/";
            navigate(path, { replace: true });
          } catch (e) {
            console.error("OIDC callback error:", e);
            navigate("/", { replace: true });
          }
          setLoading(false);
          return;
        }

        // Fast path: check if we have a valid (non-expired) session in storage.
        // This is a synchronous read from sessionStorage — no network calls.
        const existingUser = await mgr.getUser();
        if (existingUser && !existingUser.expired) {
          setUser(existingUser);
          setLoading(false);
          return;
        }

        // Expired token: try a quick silent renew (iframe to IdP).
        // Only attempt this if we HAD a user (meaning the IdP session might still be alive).
        // Skip if no prior session — go straight to redirect (faster than waiting for iframe timeout).
        if (existingUser && existingUser.expired) {
          try {
            const renewedUser = await mgr.signinSilent();
            if (renewedUser) {
              setUser(renewedUser);
              setLoading(false);
              return;
            }
          } catch {
            // Silent renew failed — IdP session expired too. Fall through to redirect.
          }
        }

        // No valid session — redirect to IdP login immediately.
        sessionStorage.setItem(
          "auth_return_to",
          window.location.pathname + window.location.search,
        );
        await mgr.signinRedirect();
      } catch (e) {
        console.error("Auth initialization error:", e);
        setLoading(false);
      }
    })();
  }, []);

  // Listen for token renewal events (automatic silent renew).
  useEffect(() => {
    const mgr = managerRef.current;
    if (!mgr) return;

    const onUserLoaded = (u: User) => setUser(u);
    const onUserUnloaded = () => setUser(null);
    const onSilentRenewError = () => {
      // Token refresh failed — redirect to login.
      sessionStorage.setItem(
        "auth_return_to",
        window.location.pathname + window.location.search,
      );
      mgr.signinRedirect();
    };

    mgr.events.addUserLoaded(onUserLoaded);
    mgr.events.addUserUnloaded(onUserUnloaded);
    mgr.events.addSilentRenewError(onSilentRenewError);

    return () => {
      mgr.events.removeUserLoaded(onUserLoaded);
      mgr.events.removeUserUnloaded(onUserUnloaded);
      mgr.events.removeSilentRenewError(onSilentRenewError);
    };
  }, [backend]);

  const login = useCallback(() => {
    const mgr = managerRef.current;
    if (mgr) {
      sessionStorage.setItem(
        "auth_return_to",
        window.location.pathname + window.location.search,
      );
      mgr.signinRedirect();
    }
  }, []);

  const logout = useCallback(() => {
    const mgr = managerRef.current;
    if (mgr) {
      mgr.signoutRedirect();
    }
  }, []);

  const token = user?.access_token || "";

  // After the initial auth phase resolves, fetch the principal to learn
  // whether the user is an admin (used to gate the audit nav + page).
  // Re-runs when the token changes so silent-renew picks up claim changes.
  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    (async () => {
      try {
        const principal = await api.whoami();
        if (cancelled) return;
        setIsAdmin(principal.is_admin);
        setPrincipalId(principal.id);
        setPrincipalGroups(principal.groups ?? []);
      } catch {
        // 401 on api_key/jwt with no creds, or 5xx — default to non-admin.
        if (cancelled) return;
        setIsAdmin(false);
        setPrincipalId("");
        setPrincipalGroups([]);
      } finally {
        if (!cancelled) setPrincipalLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loading, token]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        token,
        login,
        logout,
        backend,
        principalLoaded,
        isAdmin,
        principalId,
        principalGroups,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
