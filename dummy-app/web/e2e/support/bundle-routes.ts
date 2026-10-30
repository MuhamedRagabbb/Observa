/** Only the operator entry point and single, safe asset filenames can replace a response. */
export function operatorBundleFile(method: string, pathname: string): string | null {
  if (method !== "GET" && method !== "HEAD") return null;
  if (pathname === "/ui/next/") return "index.html";
  const match = /^\/ui\/next\/((?:assets|licenses)\/([A-Za-z0-9][A-Za-z0-9._-]*))$/.exec(pathname);
  if (!match || match[2]!.includes("..")) return null;
  const file = match[1]!;
  if (file.startsWith("assets/") && /^[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{6,}\.(?:js|css|woff2?|svg|png|jpe?g|webp|ico)$/.test(match[2]!)) return file;
  if (file.startsWith("licenses/") && /^[A-Za-z0-9][A-Za-z0-9._-]*\.txt$/.test(match[2]!)) return file;
  return null;
}

export function operatorBundleActive({ localBundle, controller, allowedOrigins }: {
  localBundle: boolean;
  controller: string;
  allowedOrigins: readonly string[];
}): boolean {
  return localBundle && controller !== "" && allowedOrigins.includes(controller);
}
