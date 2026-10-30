import createClient, { type Client, type ClientOptions } from "openapi-fetch";

export type ClientSetup = {
  /** Replaces the global fetch, for tests. */
  fetch?: ClientOptions["fetch"];
};

/**
 * A typed client for the origin that served the page. Each bundle is served by the API
 * it calls (store by the Gateway, operator by the controller), so there is no base URL
 * to configure and no cross-origin request. Paths are absolute from the origin, so a
 * page always calls /api/... from that origin, independent of its page path.
 */
export function sameOriginClient<Paths extends {}>(setup: ClientSetup = {}): Client<Paths> {
  return createClient<Paths>({
    baseUrl: window.location.origin,
    ...(setup.fetch ? { fetch: setup.fetch } : {}),
  });
}
