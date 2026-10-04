import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type Data = Record<string, any>;
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details: unknown,
  ) {
    super(message);
  }
}
export async function request<T = Data>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail ?? payload?.error ?? payload?.message;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `Request failed (${response.status})`,
      response.status,
      payload,
    );
  }
  return payload as T;
}
export function useApi<T = Data>(path: string, enabled = true) {
  return useQuery({
    queryKey: [path],
    queryFn: ({ signal }) => request<T>(path, undefined, signal),
    enabled,
    refetchInterval: path === "/session" ? false : path === "/tests" ? 2000 : 15000,
  });
}
export function useAction(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => request(path, body),
    onSuccess: () => client.invalidateQueries(),
  });
}
export function rows(data: unknown, ...keys: string[]): Data[] {
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object") {
    for (const key of keys) {
      const value = (data as Data)[key];
      if (Array.isArray(value)) return value;
    }
  }
  return [];
}
export function short(value: unknown, length = 12) {
  const text = String(value ?? "Unavailable");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
export function number(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US")
    : "n/a";
}
export function money(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(value / 1_000_000)
    : "n/a";
}
export function time(value: unknown) {
  if (!value) return "Not recorded";
  const date = new Date(String(value));
  return Number.isNaN(date.valueOf())
    ? String(value)
    : date.toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}
export function decision(operation: Data) {
  return typeof operation.decision === "string"
    ? operation.decision
    : (operation.decision?.decision ??
        operation.decision?.action ??
        operation.decision?.outcome ??
        "unknown");
}
