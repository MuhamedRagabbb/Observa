// P4-C: the cart. Add to cart and Sold out on the collection, the header's Cart, and the cart
// (drawer at 1440, full-height sheet on phones) in every state. /api/products and
// /api/cart/quote are answered by typed mocks built from the seeded catalogue.
import type { Page } from "@playwright/test";
import { PRODUCTS, quoteFor } from "./fixtures/gateway.ts";
import { STORE_URL, expect, test, type MockApi } from "./support/harness.ts";

type Line = { product_id: string; quantity: number };

const isDesktop = (projectName: string) => projectName === "desktop-1440";
const READY: Line[] = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
  { product_id: "sku-003", quantity: 1 },
];
const WITH_UNAVAILABLE: Line[] = [...READY, { product_id: "sku-004", quantity: 1 }];

const serveCatalogue = (api: MockApi) => api.onGateway("GET", "/api/products", () => ({ status: 200, body: PRODUCTS }));
const serveQuotes = (api: MockApi) =>
  api.onGateway("POST", "/api/cart/quote", (request) => ({
    status: 200,
    body: quoteFor((request.body as { items: Line[] }).items),
  }));

/** Stores a cart before the page loads, as an earlier visit would have. */
async function seedCart(page: Page, lines: Line[]) {
  await page.addInitScript((value) => {
    if (!sessionStorage.getItem("seeded")) {
      localStorage.setItem("observa.cart", value);
      sessionStorage.setItem("seeded", "1");
    }
  }, JSON.stringify({ version: 1, lines }));
}

const cartButton = (page: Page) => page.getByRole("banner").getByRole("button", { name: /^Cart,/ });
const dialog = (page: Page) => page.getByRole("dialog", { name: "Cart" });

async function openCart(page: Page) {
  await expect(cartButton(page)).toBeVisible();
  await cartButton(page).click();
  await expect(dialog(page)).toBeVisible();
}

/** Lets the slide-in finish and every thumbnail load, so captures show the settled cart. */
async function settle(page: Page) {
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished)));
  for (const image of await dialog(page).locator("img").all()) await expect(image).toHaveJSProperty("complete", true);
}

test.describe("collection cart actions", () => {
  test("Add to cart adds a unit, and Sold out cannot be pressed", async ({ page, api }) => {
    serveCatalogue(api);
    await page.goto(STORE_URL);
    await expect(cartButton(page)).toHaveAccessibleName("Cart, 0 items");
    await page.getByRole("button", { name: "Add to cart: Coffee Mug" }).click();
    await page.getByRole("button", { name: "Add to cart: Coffee Mug" }).click();
    await page.getByRole("button", { name: "Add to cart: Pen Set" }).click();
    await expect(cartButton(page)).toHaveAccessibleName("Cart, 3 items");
    await expect(cartButton(page)).toContainText("3");
    await expect(page.getByRole("button", { name: "Sold out: Limited Poster / Art Print" })).toBeDisabled();
    // The cart survives a reload.
    await page.reload();
    await expect(cartButton(page)).toHaveAccessibleName("Cart, 3 items");
  });

  test("two tabs share one cart", async ({ page, api, context }) => {
    serveCatalogue(api);
    await page.goto(STORE_URL);
    const other = await context.newPage();
    await other.goto(STORE_URL);
    await expect(cartButton(other)).toHaveAccessibleName("Cart, 0 items");
    await page.getByRole("button", { name: "Add to cart: Journal / Notebook" }).click();
    await expect(cartButton(other)).toHaveAccessibleName("Cart, 1 item");
    await other.getByRole("button", { name: "Add to cart: Organizing Labels" }).click();
    await expect(cartButton(page)).toHaveAccessibleName("Cart, 2 items");
    await other.close();
  });
});

test.describe("cart states", () => {
  test("ready", async ({ page, api, capture }, info) => {
    serveCatalogue(api);
    serveQuotes(api);
    await seedCart(page, READY);
    await page.goto(STORE_URL);
    await openCart(page);
    const cart = dialog(page);
    await expect(cart.getByText("4 items", { exact: true })).toBeVisible();
    await expect(cart.getByText("2 × $18.00 each")).toBeVisible();
    await expect(cart.getByText("$54.00")).toBeVisible();
    await expect(cart.getByRole("button", { name: /checkout/i })).toHaveCount(0);
    await settle(page); // measure after the slide-in
    const box = await cart.boundingBox();
    if (!box) throw new Error("cart not laid out");
    const viewport = page.viewportSize() ?? { width: 0, height: 0 };
    if (isDesktop(info.project.name)) {
      // Drawer artboards: a 460px panel against the right edge, full height.
      expect(Math.round(box.width)).toBe(460);
      expect(Math.round(box.x + box.width)).toBe(viewport.width);
    } else {
      // Cart sheet 375: the whole screen.
      expect(Math.round(box.width)).toBe(viewport.width);
    }
    expect(Math.round(box.height)).toBe(viewport.height);
    await settle(page);
    await capture("cart-ready", "viewport");
  });

  test("with an unavailable item", async ({ page, api, capture }) => {
    serveCatalogue(api);
    serveQuotes(api);
    await seedCart(page, WITH_UNAVAILABLE);
    await page.goto(STORE_URL);
    await openCart(page);
    const cart = dialog(page);
    await expect(cart.getByText("4 items · 1 unavailable")).toBeVisible();
    await expect(cart.getByText("1 item is out of stock.")).toBeVisible();
    await expect(cart.getByText("Remove unavailable items to continue.")).toBeVisible();
    await expect(cart.getByRole("button", { name: "Increase quantity of Limited Poster / Art Print" })).toBeDisabled();
    await settle(page);
    await capture("cart-unavailable", "viewport");
    await cart.getByRole("button", { name: "Remove unavailable items" }).click();
    await expect(cart.getByRole("heading", { name: "Limited Poster / Art Print" })).toHaveCount(0);
    await expect(cart.getByText("Remove unavailable items to continue.")).toHaveCount(0);
    await expect(cart.getByRole("heading", { name: "Cart" })).toBeFocused();
  });

  test("empty", async ({ page, api, capture }) => {
    serveCatalogue(api);
    await page.goto(STORE_URL);
    await openCart(page);
    await expect(dialog(page).getByText("Your cart is empty.")).toBeVisible();
    await settle(page);
    await capture("cart-empty", "viewport");
    await dialog(page).getByRole("link", { name: "Shop the collection" }).click();
    await expect(dialog(page)).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 2, name: "The Collection" })).toBeFocused();
    expect(api.calls.filter((call) => call.path === "/api/cart/quote")).toEqual([]);
  });

  test("loading", async ({ page, api, capture }) => {
    serveCatalogue(api);
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    api.onGateway("POST", "/api/cart/quote", async (request) => {
      await held;
      return { status: 200, body: quoteFor((request.body as { items: Line[] }).items) };
    });
    await seedCart(page, READY);
    await page.goto(STORE_URL);
    await openCart(page);
    await expect(dialog(page)).toHaveAttribute("aria-busy", "true");
    await expect(dialog(page).getByRole("status")).toHaveText("Loading your cart…");
    await settle(page);
    await capture("cart-loading", "viewport");
    release();
    await expect(dialog(page).getByText("$54.00")).toBeVisible();
  });

  test("error, then Try again", async ({ page, api, capture }) => {
    serveCatalogue(api);
    let failing = true;
    api.onGateway("POST", "/api/cart/quote", (request) =>
      failing
        ? { status: 503, body: { error_code: "dependency_unavailable", message: "Inventory unavailable" } }
        : { status: 200, body: quoteFor((request.body as { items: Line[] }).items) },
    );
    await seedCart(page, READY);
    await page.goto(STORE_URL);
    await openCart(page);
    const alert = dialog(page).getByRole("alert");
    await expect(alert).toContainText("Your cart couldn’t load.");
    await settle(page);
    await capture("cart-error", "viewport");
    failing = false;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(dialog(page).getByText("$54.00")).toBeVisible();
    const quotes = api.calls.filter((call) => call.path === "/api/cart/quote");
    expect(quotes).toHaveLength(2);
    for (const call of quotes) expect(call.headers["x-request-id"]).toMatch(/^web-[0-9a-f-]{36}$/);
  });
});

test.describe("cart behaviour", () => {
  test("quantity changes ask for a new quote within the limits", async ({ page, api }) => {
    serveCatalogue(api);
    serveQuotes(api);
    await seedCart(page, [{ product_id: "sku-002", quantity: 9 }]);
    await page.goto(STORE_URL);
    await openCart(page);
    const cart = dialog(page);
    await cart.getByRole("button", { name: "Increase quantity of Coffee Mug" }).click();
    await expect(cart.getByText("10 × $18.00 each")).toBeVisible();
    await expect(cart.getByRole("button", { name: "Increase quantity of Coffee Mug" })).toHaveAttribute("aria-disabled", "true");
    const bodies = api.calls.filter((call) => call.path === "/api/cart/quote").map((call) => call.body);
    expect(bodies.at(-1)).toEqual({ items: [{ product_id: "sku-002", quantity: 10 }] });
    await cart.getByRole("button", { name: "Close cart" }).click();
    await expect(page.getByRole("button", { name: "10 in cart: Coffee Mug, the most per order" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  test("keyboard: focus moves in, Tab stays inside, Escape returns focus to Cart", async ({ page, api }) => {
    serveCatalogue(api);
    serveQuotes(api);
    await seedCart(page, READY);
    await page.goto(STORE_URL);
    await expect(cartButton(page)).toBeVisible();
    await cartButton(page).focus();
    await page.keyboard.press("Enter");
    await expect(dialog(page).getByRole("heading", { name: "Cart" })).toBeFocused();
    await expect(dialog(page).getByText("$54.00")).toBeVisible();
    for (let i = 0; i < 20; i += 1) {
      await page.keyboard.press(i % 3 === 0 ? "Shift+Tab" : "Tab");
      const inside = await page.evaluate(() => Boolean(document.activeElement?.closest("[role='dialog']")));
      expect(inside).toBe(true);
    }
    const ring = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
    expect(ring).toBe("solid");
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toHaveCount(0);
    await expect(cartButton(page)).toBeFocused();
  });

  test("the page behind does not scroll or respond while the cart is open", async ({ page, api }) => {
    serveCatalogue(api);
    serveQuotes(api);
    await page.goto(STORE_URL);
    const brand = page.getByRole("banner").getByRole("link", { name: "OBSERVA" });
    await expect(brand).toBeVisible();
    const before = await brand.boundingBox();
    await openCart(page);
    // The page keeps its place: no sideways jump when the scrollbar goes away.
    expect((await brand.boundingBox())?.x).toBe(before?.x);
    await page.mouse.wheel(0, 1200);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.evaluate(() => document.querySelector("#root")?.hasAttribute("inert"))).toBe(true);
    await page.mouse.click(5, 5); // the dimmed page closes the cart, like the close button
    await expect(dialog(page)).toHaveCount(0);
    expect(await page.evaluate(() => document.querySelector("[inert]"))).toBeNull();
  });

  test("slides in, except under reduced motion", async ({ page, api }) => {
    serveCatalogue(api);
    await page.goto(STORE_URL);
    await openCart(page);
    expect(await dialog(page).evaluate((el) => getComputedStyle(el).animationDuration)).toBe("0.24s");
    await page.keyboard.press("Escape");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openCart(page);
    expect(await dialog(page).evaluate((el) => getComputedStyle(el).animationDuration)).toBe("0s");
  });
});
