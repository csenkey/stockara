export interface AuthConfig {
  apiBaseUrl: string;
  cognitoDomain: string;
  clientId: string;
  redirectUri: string;
  logoutUri: string;
  socialProviders: string[];
}

export interface AuthSession {
  accessToken: string;
  idToken: string;
  email?: string;
}

const SESSION_KEY = "stockara.auth.session";
const VERIFIER_KEY = "stockara.auth.verifier";
const STATE_KEY = "stockara.auth.state";

export async function loadAuthConfig(): Promise<AuthConfig> {
  const response = await fetch("/auth-config.json", { cache: "no-store" });
  if (!response.ok) throw new Error("Authentication is not configured.");
  return response.json() as Promise<AuthConfig>;
}

export function storedSession(): AuthSession | null {
  const raw = sessionStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw) as AuthSession;
    const claims = decodeJwt(session.idToken);
    if (Number(claims.exp ?? 0) * 1000 <= Date.now()) {
      sessionStorage.removeItem(SESSION_KEY);
      return null;
    }
    return { ...session, email: String(claims.email ?? "") || undefined };
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export async function beginAuth(config: AuthConfig, register = false) {
  const verifier = randomUrlSafe(64);
  const state = randomUrlSafe(32);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const challenge = await sha256UrlSafe(verifier);
  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: config.redirectUri,
    code_challenge_method: "S256",
    code_challenge: challenge,
    state,
  });
  const path = register ? "/signup" : "/oauth2/authorize";
  window.location.assign(`${config.cognitoDomain}${path}?${params}`);
}

export async function completeAuthCallback(config: AuthConfig): Promise<AuthSession | null> {
  const params = new URLSearchParams(window.location.search);
  const authError = params.get("error_description") ?? params.get("error");
  if (authError) throw new Error(authError);
  const code = params.get("code");
  if (!code) return storedSession();
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const expectedState = sessionStorage.getItem(STATE_KEY);
  if (!verifier || !expectedState || params.get("state") !== expectedState) {
    throw new Error("The login session expired or could not be verified. Please try again.");
  }
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const response = await fetch(`${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) throw new Error("Cognito could not complete login.");
  const tokens = (await response.json()) as { access_token: string; id_token: string };
  const session: AuthSession = {
    accessToken: tokens.access_token,
    idToken: tokens.id_token,
    email: String(decodeJwt(tokens.id_token).email ?? "") || undefined,
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
  return session;
}

export function logout(config: AuthConfig) {
  sessionStorage.removeItem(SESSION_KEY);
  const params = new URLSearchParams({
    client_id: config.clientId,
    logout_uri: config.logoutUri,
  });
  window.location.assign(`${config.cognitoDomain}/logout?${params}`);
}

function decodeJwt(token: string): Record<string, unknown> {
  const value = token.split(".")[1]?.replace(/-/g, "+").replace(/_/g, "/");
  if (!value) throw new Error("Invalid token");
  return JSON.parse(atob(value)) as Record<string, unknown>;
}

function randomUrlSafe(length: number) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return base64Url(bytes);
}

async function sha256UrlSafe(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return base64Url(new Uint8Array(digest));
}

function base64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((value) => (binary += String.fromCharCode(value)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
