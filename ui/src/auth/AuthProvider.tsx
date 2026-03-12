import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { UserManager, type User } from "oidc-client-ts";
import { setApiAuthToken } from "../api/client";

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
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  token: "",
  login: () => {},
  logout: () => {},
  backend: "noop",
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
  const managerRef = useRef<UserManager | null>(null);
  const initRef = useRef(false);

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
          // Keycloak needs this for public clients doing auth code flow
          // If the client is confidential, PKCE is still fine
          loadUserInfo: true,
        });
        managerRef.current = mgr;

        // Handle callback if we're on the callback path
        if (window.location.pathname === "/ui/callback") {
          try {
            const callbackUser = await mgr.signinRedirectCallback();
            setUser(callbackUser);
            // Navigate to the original page or dashboard
            const returnTo =
              sessionStorage.getItem("auth_return_to") || "/ui/";
            sessionStorage.removeItem("auth_return_to");
            window.history.replaceState({}, "", returnTo);
          } catch (e) {
            console.error("OIDC callback error:", e);
            // Redirect to dashboard on callback failure
            window.history.replaceState({}, "", "/ui/");
          }
          setLoading(false);
          return;
        }

        // Check if we already have a session
        const existingUser = await mgr.getUser();
        if (existingUser && !existingUser.expired) {
          setUser(existingUser);
          setLoading(false);
          return;
        }

        // Try silent renew first
        try {
          const renewedUser = await mgr.signinSilent();
          if (renewedUser) {
            setUser(renewedUser);
            setLoading(false);
            return;
          }
        } catch {
          // Silent renew failed — need interactive login
        }

        // No valid session — redirect to login
        sessionStorage.setItem("auth_return_to", window.location.pathname + window.location.search);
        await mgr.signinRedirect();
      } catch (e) {
        console.error("Auth initialization error:", e);
        setLoading(false);
      }
    })();
  }, []);

  // Listen for token renewal
  useEffect(() => {
    const mgr = managerRef.current;
    if (!mgr) return;

    const onUserLoaded = (u: User) => setUser(u);
    const onUserUnloaded = () => setUser(null);

    mgr.events.addUserLoaded(onUserLoaded);
    mgr.events.addUserUnloaded(onUserUnloaded);

    return () => {
      mgr.events.removeUserLoaded(onUserLoaded);
      mgr.events.removeUserUnloaded(onUserUnloaded);
    };
  }, [backend]);

  const login = useCallback(() => {
    const mgr = managerRef.current;
    if (mgr) {
      sessionStorage.setItem("auth_return_to", window.location.pathname + window.location.search);
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

  return (
    <AuthContext.Provider
      value={{ user, loading, token, login, logout, backend }}
    >
      {children}
    </AuthContext.Provider>
  );
}
