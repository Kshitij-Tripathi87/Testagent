const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export type ApiOptions = RequestInit & {
  accessToken?: string | null;
  devUser?: string | null;
};

export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { accessToken, devUser, ...requestOptions } = options;
  const headers = new Headers(requestOptions.headers || {});
  if (accessToken) headers.set("Authorization", "Bearer " + accessToken);
  else if (devUser) headers.set("X-Dev-User", devUser);
  if (requestOptions.body && !(requestOptions.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(API_BASE + path, {
    ...requestOptions,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    let message = "Request failed (" + response.status + ")";
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    } catch {
      // Keep the HTTP status message when the response is not JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function downloadArtifact(
  path: string,
  accessToken?: string | null,
  devUser?: string | null,
): Promise<void> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", "Bearer " + accessToken);
  else if (devUser) headers.set("X-Dev-User", devUser);
  const response = await fetch(API_BASE + path, { headers, cache: "no-store" });
  if (!response.ok) throw new Error("Could not download artifact");
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = response.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1] || "artifact.txt";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}
