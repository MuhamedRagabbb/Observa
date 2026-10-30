// Shared helpers for the checkout specs (checkout.spec.ts, recovery.spec.ts): typed results,
// a Gateway mock that counts every checkout POST and every Check status request, stored-state
// seeding (localStorage cart, IndexedDB gate) and readers for what the browser holds.
import type { BrowserContext, Page } from "@playwright/test";
import type { components } from "../../src/api/generated/gateway.ts";
import { PRODUCTS, quoteFor } from "../fixtures/gateway.ts";
import { STORE_URL, expect, type MockApi, type MockReply } from "./harness.ts";

export type Line = { product_id: string; quantity: number };
export type CheckoutResult = components["schemas"]["CheckoutResult"];
export type GateRecord = { attempt_ref: string; customer_id: string; lines: Line[]; created_at: string; status: "submitting" | "processing" | "unknown" };

export const CHECKOUT_URL = `${STORE_URL}checkout`;
export const CUSTOMER = "web-0123456789abcdefghijkl";
export const READY: Line[] = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
  { product_id: "sku-003", quantity: 1 },
];
export const isDesktop = (projectName: string) => projectName === "desktop-1440";

export function result(ref: string, outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: ref,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: { confirmed: "completed", processing: "charging", payment_failed: "payment_declined", unavailable: "not_placed", failed: "failed", abandoned: "abandoned" }[outcome],
    outcome,
    retryable: outcome === "payment_failed" || outcome === "abandoned" || (outcome === "failed" && extra.retryable !== false),
    items: READY.map((line) => ({
      ...line,
      unit_price_cents: PRODUCTS.find((p) => p.product_id === line.product_id)?.price_cents ?? null,
    })),
    total_cents: 5400,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-27T10:00:00Z",
    ...extra,
  };
}

/** What resolve returns after fencing a reference nothing was recorded for. */
export const fenced = (ref: string) => result(ref, "abandoned", { items: [], total_cents: null, order_id: null });

export const envelope = (code: string, details?: CheckoutResult) => ({ error_code: code, message: "Checkout was not completed", ...(details ? { details } : {}) });

type Reply = (ref: string) => MockReply | Promise<MockReply>;

/** Catalogue, quotes and checkout; returns the references of every checkout POST seen. */
export function serve(api: MockApi, reply: Reply) {
  const refs: string[] = [];
  api.onGateway("GET", "/api/products", () => ({ status: 200, body: PRODUCTS }));
  api.onGateway("POST", "/api/cart/quote", (request) => ({ status: 200, body: quoteFor((request.body as { items: Line[] }).items) }));
  api.onGateway("POST", "/api/checkout", (request) => {
    const ref = request.headers["idempotency-key"] ?? "";
    refs.push(ref);
    return reply(ref);
  });
  return refs;
}

/** Check status (GET) and resolve (POST); returns every request, as "GET ref" or "RESOLVE ref". */
export function serveStatus(api: MockApi, { get, resolve }: { get?: Reply; resolve?: Reply }) {
  const seen: string[] = [];
  const queries: string[] = [];
  api.onGateway("GET", "/api/attempts/{attempt_ref}", (request, params) => {
    const ref = params.attempt_ref ?? "";
    seen.push(`GET ${ref}`);
    queries.push(request.query.toString());
    return get ? get(ref) : { status: 503 };
  });
  api.onGateway("POST", "/api/attempts/{attempt_ref}/resolve", (request, params) => {
    const ref = params.attempt_ref ?? "";
    seen.push(`RESOLVE ${ref}`);
    queries.push(request.query.toString());
    return resolve ? resolve(ref) : { status: 503 };
  });
  return { seen, queries, count: (kind: "GET" | "RESOLVE") => seen.filter((s) => s.startsWith(kind)).length };
}

/** Stores a cart (and the anonymous customer ID) once per tab, as an earlier visit would have. */
export async function seed(page: Page, lines: Line[]) {
  await page.addInitScript(
    ({ cart, customer }) => {
      if (sessionStorage.getItem("seeded")) return;
      localStorage.setItem("observa.cart", cart);
      localStorage.setItem("observa.customer", customer);
      sessionStorage.setItem("seeded", "1");
    },
    { cart: JSON.stringify({ version: 1, lines }), customer: CUSTOMER },
  );
}

/**
 * Stores attempts in the IndexedDB gate once per browser context, before the app opens it (the
 * app's open waits for this one), as an earlier visit that sent them would have.
 */
export async function seedGate(target: Page | BrowserContext, records: GateRecord[]) {
  await target.addInitScript((attempts) => {
    if (localStorage.getItem("seeded-gate")) return;
    localStorage.setItem("seeded-gate", "1");
    const open = indexedDB.open("observa-checkout", 2);
    open.onupgradeneeded = () => {
      const db = open.result;
      db.createObjectStore("attempts", { keyPath: "attempt_ref" });
      db.createObjectStore("meta");
      db.createObjectStore("effects", { keyPath: "attempt_ref" });
    };
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction("attempts", "readwrite");
      for (const record of attempts) tx.objectStore("attempts").put(record);
      tx.oncomplete = () => db.close();
    };
  }, records);
}

export const gateRecord = (attempt_ref: string, status: GateRecord["status"], lines: Line[] = READY): GateRecord => ({
  attempt_ref,
  customer_id: CUSTOMER,
  lines,
  created_at: "2026-10-27T10:00:00.000Z",
  status,
});

export async function fill(page: Page) {
  await page.getByLabel("Full name").fill("Demo Name");
  await page.getByLabel("Email").fill("demo@example.com");
}

export const placeButton = (page: Page) => page.getByRole("button", { name: /^Place Order/ });
export const checkButton = (page: Page) => page.getByRole("button", { name: /^Check (Order Status|again)$/ });

export async function place(page: Page) {
  await fill(page);
  await expect(placeButton(page)).not.toHaveAttribute("aria-disabled", "true");
  await placeButton(page).click();
}

export const storedAttempts = (page: Page) =>
  page.evaluate(() => JSON.parse(localStorage.getItem("observa.attempts") ?? '{"attempts":[]}').attempts as { attempt_ref: string; status: string }[]);
export const storedCart = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem("observa.cart") ?? '{"lines":[]}').lines as Line[]);

/** Everything the IndexedDB gate holds: attempts and the journal of confirmed cart effects. */
export const gateContents = (page: Page) =>
  page.evaluate(
    () =>
      new Promise<{ attempts: unknown[]; effects: unknown[] }>((resolve) => {
        const open = indexedDB.open("observa-checkout");
        open.onsuccess = () => {
          const db = open.result;
          const names = ["attempts", "effects"].filter((name) => db.objectStoreNames.contains(name));
          if (names.length === 0) return resolve({ attempts: [], effects: [] });
          const tx = db.transaction(names, "readonly");
          const out = { attempts: [] as unknown[], effects: [] as unknown[] };
          for (const name of names) {
            const request = tx.objectStore(name).getAll();
            request.onsuccess = () => (out[name as "attempts" | "effects"] = request.result);
          }
          tx.oncomplete = () => {
            db.close();
            resolve(out);
          };
        };
      }),
  );

export async function settled(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  for (const image of await page.locator("main img").all()) {
    if (await image.isVisible()) await expect(image).toHaveJSProperty("complete", true);
  }
}

/** Presses Place Order in every tab at the same moment (one shared wall-clock instant). */
export async function pressTogether(tabs: Page[]) {
  const at = Date.now() + 400;
  await Promise.all(
    tabs.map((tab) =>
      tab.evaluate((when) => {
        setTimeout(() => document.querySelector<HTMLButtonElement>("form button[type=submit]")?.click(), Math.max(0, when - Date.now()));
      }, at),
    ),
  );
  await new Promise((resolve) => setTimeout(resolve, Math.max(0, at - Date.now()) + 50));
}
