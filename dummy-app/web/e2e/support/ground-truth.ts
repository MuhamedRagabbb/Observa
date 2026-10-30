const TOKENS = /root_cause|root_cause_service|expected_signals/;

/** Redirect bodies are unavailable to Playwright and must never create false leak failures. */
export function shouldInspectJson(status: number, contentType: string | undefined): boolean {
  if (status >= 300 && status <= 399) return false;
  const mime = contentType?.split(";", 1)[0]?.trim().toLowerCase();
  return mime !== undefined && (mime.endsWith("/json") || mime.endsWith("+json"));
}

/** Scan decoded JSON iteratively; keys and values can both expose evaluation ground truth. */
export function groundTruthLeaks(value: unknown): string[] {
  const leaks = new Set<string>();
  const visited = new Set<object>();
  const pending: { value: unknown; path: string }[] = [{ value, path: "" }];
  while (pending.length > 0) {
    const current = pending.pop()!;
    if (typeof current.value === "string") {
      if (TOKENS.test(current.value)) leaks.add(current.path || "$");
    } else if (current.value !== null && typeof current.value === "object" && !visited.has(current.value)) {
      visited.add(current.value);
      if (Array.isArray(current.value)) {
        for (let i = current.value.length - 1; i >= 0; i--) {
          pending.push({ value: current.value[i], path: `${current.path}[${i}]` });
        }
      } else {
        for (const [key, child] of Object.entries(current.value).reverse()) {
          const path = current.path ? `${current.path}.${key}` : key;
          if (TOKENS.test(key)) leaks.add(path);
          pending.push({ value: child, path });
        }
      }
    }
  }
  return [...leaks];
}
