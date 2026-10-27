// P4-0 harness checks, run against the production builds served by vite preview, with every
// API request answered by typed mocks.
import { PRODUCTS } from "./fixtures/gateway.ts";
import { OPERATOR_URL, STORE_URL, expect, test } from "./support/harness.ts";

// The store home reads its products on load; every test here answers that request.
test.beforeEach(({ api }) => {
  api.onGateway("GET", "/api/products", () => ({ status: 200, body: PRODUCTS }));
});

test.describe("bundle shells", () => {
  test("the storefront shell links to Experiments on the preview controller port", async ({ page, capture }) => {
    await page.goto(STORE_URL);
    await expect(page.getByRole("main", { name: "Storefront" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Experiments" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:18090/ui/next/",
    );
    await capture("shell-store");
  });

  test("the operator shell links back to the storefront on the preview gateway port", async ({ page, capture }) => {
    await page.goto(OPERATOR_URL);
    await expect(page.getByRole("link", { name: "Storefront" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:18000/",
    );
    await capture("shell-operator");
  });
});

test.describe("harness guards", () => {
  test("an API call without a mock is reported and never reaches a server", async ({ page, api }) => {
    await page.goto(STORE_URL);
    const outcome = await page.evaluate(() => fetch("/api/orders").then((r) => r.status, () => "failed"));
    expect(outcome).toBe("failed");
    expect(api.takeViolations()).toEqual(["unmocked GET /api/orders"]);
  });

  test("a request to another origin, such as the live gateway, is blocked and reported", async ({ page, api }) => {
    await page.goto(STORE_URL);
    const outcome = await page.evaluate(() =>
      fetch("http://127.0.0.1:8000/api/products").then((r) => r.status, () => "failed"),
    );
    expect(outcome).toBe("failed");
    expect(api.takeViolations()).toEqual(["cross-origin GET http://127.0.0.1:8000/api/products"]);
  });

  test("a mocked route answers with typed data and is recorded", async ({ page, api }) => {
    api.onGateway("GET", "/api/products", () => ({ status: 200, body: [] }));
    await page.goto(STORE_URL);
    const status = await page.evaluate(() => fetch("/api/products").then((r) => r.status));
    expect(status).toBe(200);
    expect(api.calls.map((c) => `${c.method} ${c.path}`)).toContain("GET /api/products");
  });

  test("a controller mock never answers the storefront's origin", async ({ page, api }) => {
    api.onController("GET", "/faults/status", () => ({ status: 200, body: {} }));
    await page.goto(STORE_URL);
    const outcome = await page.evaluate(() => fetch("/faults/status").then((r) => r.status, () => "failed"));
    expect(outcome).toBe("failed");
    expect(api.takeViolations()).toEqual(["unmocked GET /faults/status"]);
  });

  test("any same-origin path outside the bundle needs a mock", async ({ page, api }) => {
    await page.goto(STORE_URL);
    for (const path of ["/api", "/ui/configX", "/unavailable"]) {
      await page.evaluate((p) => fetch(p).catch(() => undefined), path);
    }
    expect(api.takeViolations()).toEqual(["unmocked GET /api", "unmocked GET /ui/configX", "unmocked GET /unavailable"]);
  });

  test("a WebSocket, for example to the live gateway, is blocked and reported", async ({ page, api }) => {
    await page.goto(STORE_URL);
    const closed = await page.evaluate(
      () =>
        new Promise<string>((resolve) => {
          const socket = new WebSocket("ws://127.0.0.1:8000/socket");
          socket.onclose = () => resolve("closed");
          socket.onopen = () => resolve("open");
        }),
    );
    expect(closed).toBe("closed");
    expect(api.takeViolations()).toEqual(["websocket ws://127.0.0.1:8000/socket"]);
  });

  test("a request body that is not JSON reaches the mock as text", async ({ page, api }) => {
    api.onGateway("POST", "/api/cart/quote", (request) => ({ status: 200, body: { echo: request.body } }));
    await page.goto(STORE_URL);
    const echo = await page.evaluate(() =>
      fetch("/api/cart/quote", { method: "POST", body: "not json" }).then((r) => r.json()),
    );
    expect(echo).toEqual({ echo: "not json" });
  });

  test("a violation nobody takes fails the test", async ({ page }) => {
    test.fail(true, "teardown must fail on a leftover violation");
    await page.goto(STORE_URL);
    await page.evaluate(() => fetch("/api/orders").catch(() => undefined));
  });

  test("serves bundle files by the real server's rules", async ({ page, api }) => {
    await page.goto(STORE_URL);
    const script = await page.locator("script[src*='/assets/']").getAttribute("src");
    const statuses = await page.evaluate(async (src) => {
      const status = (path: string) => fetch(path).then((r) => r.status);
      return {
        exact: await status(src ?? ""),
        // Windows file systems ignore case; the real server compares the exact name.
        otherCase: await status((src ?? "").toUpperCase().replace("/UI/NEXT/ASSETS/", "/ui/next/assets/")),
        missing: await status("/ui/next/assets/missing.js"),
        fileOutsideAssets: await status("/ui/next/robots.txt"),
        // P4-H: the store's pages are an explicit allowlist at /; nothing else gets the entry page.
        page: await status("/orders/OBS-1042-7F3A"),
        oldPageLink: await fetch("/ui/next/checkout", { redirect: "manual" }).then((r) => r.type),
        // Not pages: never the entry page (the server's 404 is tested in test_store_cutover.py).
        notAReference: await status("/orders/OBS-1").catch(() => "not served"),
        notAPage: await status("/cart").catch(() => "not served"),
      };
    }, script);
    expect(statuses).toEqual({
      exact: 200,
      otherCase: 404,
      missing: 404,
      fileOutsideAssets: 404,
      page: 200,
      oldPageLink: "opaqueredirect",
      notAReference: "not served",
      notAPage: "not served",
    });
    expect(api.takeViolations()).toEqual(["unmocked GET /orders/OBS-1", "unmocked GET /cart"]);
  });

  test("reduced motion can be emulated for motion checks", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(STORE_URL);
    const duration = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--motion-duration-base").trim(),
    );
    expect(duration).toBe("0s");
  });
});
