import { QueryClient } from "@tanstack/react-query";

// No automatic retries by default; each screen sets its own retry rules (P4, P5).
export function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}
