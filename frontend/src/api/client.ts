import createClient from "openapi-fetch";

import type { paths } from "./schema";

export const API_PREFIX = "/builder-ui/v1";
/**
 * The router prefix, and it is part of every path this client is given.
 *
 * The generated schema carries it -- FastAPI puts a router prefix into the
 * document, and only `root_path` stays out -- so the typed path literals below
 * include it too. Stripping it here and re-adding it there would be two places
 * that have to agree.
 */

/**
 * The management API, typed from the service's own OpenAPI document.
 *
 * `import.meta.env.BASE_URL` is what Vite's `base` resolved to at build time,
 * so the client speaks to the prefix the page is served from. Hard-coding "/"
 * is the mistake that works in development and 404s behind the web frontend.
 *
 * There is no token here and there must not be: this application is
 * authenticated by the web frontend, which asserts the principal in a header
 * the browser never sees. A bearer token in a single-page app is a bearer
 * token in every viewer's developer tools.
 */
export const client = createClient<paths>({
  baseUrl: import.meta.env.BASE_URL.replace(/\/$/, ""),
});

/** The shape a `ProblemError` reaches the browser in (RFC 9457). */
export type Problem = {
  type?: string;
  title?: string;
  detail?: string;
  status?: number;
  findings?: string[];
};

/**
 * Turn whatever came back into something readable, without losing findings.
 *
 * Publishing answers `422` with a `findings` array -- every reason at once
 * rather than the first. Collapsing that into a single line is how a person
 * ends up fixing one problem per attempt.
 */
export function problemText(error: unknown): string {
  const problem = error as Problem | undefined;
  if (!problem) return "Unknown error";
  const head = problem.detail || problem.title || problem.type || "Request failed";
  return problem.findings?.length
    ? `${head}\n• ${problem.findings.join("\n• ")}`
    : head;
}
