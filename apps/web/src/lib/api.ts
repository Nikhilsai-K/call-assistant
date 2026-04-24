export const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_BASE_URL ?? "")
    : process.env.API_BASE_URL ?? "http://api:8000";

export async function api<T>(
  path: string,
  init: RequestInit = {},
  orgId = "dev-org",
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Dev-Org": orgId,
      ...(init.headers ?? {}),
    },
  });
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }
  return (await resp.json()) as T;
}
