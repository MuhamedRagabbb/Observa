import { groundTruthLeaks, shouldInspectJson } from "./ground-truth.ts";

export type ReissueScan =
  | { kind: "clean" }
  | { kind: "not-json" }
  | { kind: "leak"; paths: string[] }
  | { kind: "unreadable"; reason: string };

export interface ScannableResponse {
  status(): number;
  headers(): Record<string, string>;
  body(): Promise<Buffer>;
}

export async function scanReissuedResponse(response: ScannableResponse): Promise<ReissueScan> {
  if (!shouldInspectJson(response.status(), response.headers()["content-type"])) return { kind: "not-json" };
  let body: Buffer;
  try {
    body = await response.body();
  } catch {
    return { kind: "unreadable", reason: "body could not be read" };
  }
  if (body.length === 0) return { kind: "clean" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return { kind: "unreadable", reason: "body is not valid JSON" };
  }
  const paths = groundTruthLeaks(parsed);
  return paths.length > 0 ? { kind: "leak", paths } : { kind: "clean" };
}

export type ReissueVerdict = { deliver: boolean; violations: string[] };

export function reissueVerdict(url: string, scan: ReissueScan): ReissueVerdict {
  if (scan.kind === "leak") return { deliver: false, violations: scan.paths.map((path) => `${url}: ${path}`) };
  if (scan.kind === "unreadable") return { deliver: false, violations: [`JSON inspection failed for ${url}: ${scan.reason}`] };
  return { deliver: true, violations: [] };
}

export class ScannedRequests {
  private readonly requests = new WeakSet<object>();

  record(key: object, verdict: { deliver: boolean }): void {
    if (verdict.deliver) this.requests.add(key);
  }

  exempts(key: object): boolean {
    return this.requests.has(key);
  }
}
