import { PREVIEW_STACK_PORTS, refuseLive } from "../../preview-guard.ts";

export type NetworkBoundary = { urlPatterns: { urlPattern: string; block: boolean }[] };

function checkOrigin(origin: string): void {
  const port = /^http:\/\/127\.0\.0\.1:([1-9][0-9]*)$/.exec(origin)?.[1];
  if (!port || Number(port) < 1024 || Number(port) > 65535 || origin !== `http://127.0.0.1:${port}`) {
    throw new Error("Network boundary needs a canonical http://127.0.0.1 origin with port 1024-65535");
  }
  refuseLive("Network boundary origin", origin);
  if (PREVIEW_STACK_PORTS.has(port)) throw new Error("Network boundary controller cannot use a preview port");
}

/** CDP's ordered URLPattern allow-list. Chromium behaviour still needs the Gate A retest. */
export function networkBoundary(controllerOrigin: string, options: { exemptSentinel?: string } = {}): NetworkBoundary {
  checkOrigin(controllerOrigin);
  const sentinel = options.exemptSentinel;
  if (sentinel !== undefined) {
    checkOrigin(sentinel);
    if (sentinel === controllerOrigin) throw new Error("Network boundary sentinel must use a different origin");
  }
  return {
    urlPatterns: [
      { urlPattern: `${controllerOrigin}/*`, block: false },
      ...(sentinel === undefined ? [] : [{ urlPattern: `${sentinel}/sentinel`, block: false }]),
      ...["http", "https", "ws", "wss", "ftp"].map((scheme) => ({ urlPattern: `${scheme}://*:*/*`, block: true })),
    ],
  };
}

/** Unit-test model using Node's global URLPattern, not a claim about Chromium's matcher. */
export function isBlocked(boundary: NetworkBoundary, url: string): boolean {
  try {
    const parsed = new URL(url);
    for (const pattern of boundary.urlPatterns) {
      if (new URLPattern(pattern.urlPattern).test(parsed.href)) return pattern.block;
    }
    return false;
  } catch {
    return true;
  }
}

export async function installNetworkBoundary(
  session: { send(method: string, params?: unknown): Promise<unknown> },
  boundary: NetworkBoundary,
): Promise<void> {
  try {
    await session.send("Network.enable");
    await session.send("Network.setBlockedURLs", { urlPatterns: boundary.urlPatterns });
  } catch (error) {
    throw new Error(`Network boundary installation failed: ${String(error)}`, { cause: error });
  }
}
