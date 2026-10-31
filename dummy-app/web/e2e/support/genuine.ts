export const REISSUE_HEADER = "x-contract-reissued";

/** Label Node reissues authoritatively without mutating the browser's header map. */
export function reissueHeaders(headers: Readonly<Record<string, string>>): Record<string, string> {
  return {
    ...Object.fromEntries(Object.entries(headers).filter(([name]) => name.toLowerCase() !== REISSUE_HEADER)),
    [REISSUE_HEADER]: "route.fetch",
  };
}

/** Explicit one-use passes, bound to an exact method and URL on the controller's origin. */
export class GenuinePasses {
  private readonly passes: { method: string; url: string }[] = [];
  private readonly controllerOrigin: string;

  constructor(controllerOrigin: string) {
    this.controllerOrigin = controllerOrigin;
  }

  allow(method: string, url: string): void {
    if (method !== "GET" && method !== "POST") throw new Error("Genuine requests must use GET or POST");
    if (new URL(url).origin !== this.controllerOrigin) throw new Error("Genuine requests must stay on the controller origin");
    this.passes.push({ method, url });
  }

  take(method: string, url: string): boolean {
    const index = this.passes.findIndex((pass) => pass.method === method && pass.url === url);
    if (index < 0) return false;
    this.passes.splice(index, 1);
    return true;
  }

  pending(): number {
    return this.passes.length;
  }
}
