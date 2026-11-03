// Thin client for AI Weekly's own backend (same origin).
export interface FieldError { loc: (string | number)[]; msg: string }

export class ApiError extends Error {
  constructor(public status: number, message: string, public errors: FieldError[] = []) {
    super(message);
  }
}

type Init = Omit<RequestInit, "body"> & { json?: unknown };

export async function api<T = unknown>(path: string, init: Init = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body });
  if (!res.ok) {
    let detail = res.statusText;
    let errors: FieldError[] = [];
    try {
      const data = await res.json();
      detail = data.detail ?? data.error ?? detail;
      if (typeof detail !== "string") detail = JSON.stringify(detail);
      if (Array.isArray(data.errors)) errors = data.errors;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || "That did not work. Try again.", errors);
  }
  return (await res.json()) as T;
}

export const post = <T = unknown>(path: string, json: unknown = {}) => api<T>(path, { method: "POST", json });
export const put = <T = unknown>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

/** Links leave through the hub (a target=_blank link does nothing inside the hub's window). */
export const openUrl = (url: string) => post("/api/open", { url });
