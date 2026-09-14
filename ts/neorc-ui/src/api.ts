// The manager's API, typed from openapi.json (see `npm run generate`).
import type { paths } from "./api/schema";

export type FlowList =
  paths["/flows"]["get"]["responses"]["200"]["content"]["application/json"];

/** The body every refusal carries; `error` names the manager's exception. */
export type ErrorBody =
  paths["/flows"]["get"]["responses"]["404"]["content"]["application/json"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: ErrorBody,
  ) {
    super(`${body.error}: ${body.detail}`);
  }
}

/**
 * The API lives one level above the page: the manager serves the UI at /ui/
 * and its routes at /, and `npm run dev` serves the page at / and proxies
 * the routes. Hash routing keeps the page's path fixed either way.
 */
export function apiUrl(path: string): URL {
  return new URL(path.replace(/^\//, ""), new URL("../", location.href));
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(response.status, (await response.json()) as ErrorBody);
  }
  return (await response.json()) as T;
}

export function listFlows(): Promise<FlowList> {
  return getJson<FlowList>("/flows");
}
