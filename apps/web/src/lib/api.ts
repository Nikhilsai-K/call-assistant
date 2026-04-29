export const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_BASE_URL ?? "")
    : (process.env.API_BASE_URL ?? "http://api:8000");

type AuthInjector = () => Promise<string | null>;
let _getToken: AuthInjector | null = null;

/** Called by `<AuthBridge />` once Clerk is mounted; lets `api()` mint tokens
 *  without the caller having to pass one explicitly. */
export function setAuthTokenProvider(fn: AuthInjector | null): void {
  _getToken = fn;
}

export async function api<T>(
  path: string,
  init: RequestInit = {},
  orgIdOverride?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };

  const token = _getToken ? await _getToken() : null;
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  } else if (orgIdOverride || process.env.NEXT_PUBLIC_DEV_ORG) {
    // Dev-only path. The API rejects this header outside ENV=development.
    headers["X-Dev-Org"] = orgIdOverride || process.env.NEXT_PUBLIC_DEV_ORG || "dev-org";
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }
  return (await resp.json()) as T;
}
