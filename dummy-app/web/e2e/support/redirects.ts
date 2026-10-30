/** Preserve readable diagnostics while removing nonce query values, including encoded key spellings. */
export function redactNonces(value: string): string {
  return value.replace(/([?&])([^=&#?\s]+)=([^&#\s]*)/g, (match: string, prefix: string, key: string) => {
    try {
      return decodeURIComponent(key.replace(/\+/g, " ")).toLowerCase() === "nonce"
        ? `${prefix}${key}=[redacted]`
        : match;
    } catch {
      return match;
    }
  });
}

/** No redirect from a Node reissue may be handed to Chromium to follow internally. */
export function redirectViolation(requestUrl: string, status: number, headers: Record<string, string | undefined>): string | null {
  if (!(status >= 300 && status <= 399) || status === 304) return null;
  const location = Object.entries(headers).find(([name]) => name.toLowerCase() === "location")?.[1];
  return `redirect refused: ${status} ${redactNonces(requestUrl).slice(0, 200)} -> ${redactNonces(location || "(no location)").slice(0, 200)}`;
}
