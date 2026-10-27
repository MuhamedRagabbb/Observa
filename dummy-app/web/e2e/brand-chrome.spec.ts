// P4-A: the storefront header as shipped, and the shared components in the test-only gallery.
// Runs at 1440, 390 and 375 against production builds with mocked requests.
import type { Page } from "@playwright/test";
import { PRODUCTS } from "./fixtures/gateway.ts";
import { GALLERY_URL, STORE_URL, expect, test } from "./support/harness.ts";

const isDesktop = (projectName: string) => projectName === "desktop-1440";

/** The focused element's accessible label and whether its focus ring is drawn. */
const focused = (page: Page) =>
  page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return null;
    const style = getComputedStyle(el);
    const svgLabel = el.querySelector("svg[aria-label]")?.getAttribute("aria-label");
    return {
      name: el.getAttribute("aria-label") ?? svgLabel ?? (el.textContent ?? "").replace("↗", "").trim(),
      ring: style.outlineStyle === "solid" && style.outlineWidth === "2px",
    };
  });

const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test.describe("storefront header as shipped", () => {
  test.beforeEach(({ api }) => {
    api.onGateway("GET", "/api/products", () => ({ status: 200, body: PRODUCTS }));
  });

  test("shows the wordmark, Home, Products, My Orders, Experiments and Cart, and nothing that does not work yet", async ({ page }, info) => {
    await page.goto(STORE_URL);
    const banner = page.getByRole("banner");
    const home = banner.getByRole("link", { name: "OBSERVA" });
    await expect(home).toHaveAttribute("href", "/ui/next"); // the server redirects to /ui/next/
    await expect(banner.getByRole("link", { name: "Experiments" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:18090/ui/next/",
    );
    await expect(banner.getByRole("button", { name: "Cart, 0 items" })).toBeVisible();
    // Search (P4-G) is a button: it opens a field above the collection.
    await expect(banner.getByRole("button", { name: "Search" })).toHaveAttribute("aria-expanded", "false");
    // My Orders (P4-F): in the main navigation on wide screens, beside the cart on phones.
    await expect(banner.getByRole("link", { name: "My Orders" })).toHaveCount(1);
    await expect(banner.getByRole("link", { name: "My Orders" })).toHaveAttribute("href", "/ui/next/orders");
    const nav = page.getByRole("navigation", { name: "Main" });
    if (isDesktop(info.project.name)) {
      await expect(nav.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    } else {
      // Derived narrow header: the wordmark is the way home and the centred nav is not shown.
      await expect(nav).toBeHidden();
    }
    // Artboards: about 200px wide at 1440 and 148px at 375.
    const width = (await home.locator("svg").boundingBox())?.width ?? 0;
    if (isDesktop(info.project.name)) expect(width).toBeGreaterThan(185);
    else expect(width).toBeGreaterThan(135);
    expect(width).toBeLessThan(isDesktop(info.project.name) ? 215 : 165);
    expect(await noSideScroll(page)).toBe(true);
  });

  test("keyboard reaches each header item in order with a visible focus ring", async ({ page }, info) => {
    await page.goto(STORE_URL);
    await expect(page.getByRole("link", { name: "Experiments" })).toBeVisible();
    const expected = isDesktop(info.project.name)
      ? ["OBSERVA", "Home", "Products", "My Orders", "Experiments", "Search", "Cart, 0 items"]
      : ["OBSERVA", "My Orders", "Experiments", "Search", "Cart, 0 items"];
    const seen = [];
    for (let i = 0; i < expected.length; i += 1) {
      await page.keyboard.press("Tab");
      seen.push(await focused(page));
    }
    expect(seen).toEqual(expected.map((name) => ({ name, ring: true })));
    // One more Tab leaves the header: it holds no other focusable control.
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.activeElement?.closest("header") ?? null)).toBeNull();
  });
});

test.describe("component gallery (test-only)", () => {
  test("components in every state", async ({ page, capture }) => {
    await page.goto(GALLERY_URL);
    await expect(page.getByRole("main", { name: "Components" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sold out" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Placing order" })).toHaveAttribute("aria-busy", "true");
    expect(await noSideScroll(page)).toBe(true);
    await capture("gallery-components");
  });

  test("image tiles keep the 4:5 frame whether or not an image exists", async ({ page }) => {
    // Hold every image request so the frames are measured before any file arrives.
    let release: () => void = () => undefined;
    let heldRequests = 0;
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route("**/assets/sku-*.svg", async (route) => {
      heldRequests += 1;
      await held;
      await route.continue();
    });
    await page.goto(GALLERY_URL);
    const frames = page.getByTestId("images").locator("figure > div");
    await expect(frames).toHaveCount(6);
    const ratios = async () =>
      Promise.all((await frames.all()).map(async (frame) => {
        const box = await frame.boundingBox();
        return box ? Math.round((box.height / box.width) * 100) / 100 : 0;
      }));
    // The five product files really were held, so the first measurement precedes any image.
    await expect.poll(() => heldRequests).toBe(5);
    const before = await ratios();
    release();
    await expect(page.getByTestId("images").locator("img").first()).toHaveJSProperty("complete", true);
    expect(before).toEqual([1.25, 1.25, 1.25, 1.25, 1.25, 1.25]);
    expect(await ratios()).toEqual(before);
  });

  test("full and focused headers keep the approved layout", async ({ page, capture }, info) => {
    await page.goto(`${GALLERY_URL}?view=headers`);
    const full = page.getByTestId("header-full");
    await expect(full.getByRole("button", { name: "Cart, 2 items" })).toBeVisible();
    await expect(full.getByRole("button", { name: "Search" })).toBeVisible();
    if (isDesktop(info.project.name)) {
      // Artboards: one row, navigation centred on the page.
      const nav = await full.getByRole("navigation", { name: "Main" }).boundingBox();
      const wordmark = await full.getByRole("link", { name: "OBSERVA" }).boundingBox();
      const cart = await full.getByRole("button", { name: "Cart, 2 items" }).boundingBox();
      if (!nav || !wordmark || !cart) throw new Error("header items not laid out");
      expect(Math.abs(nav.x + nav.width / 2 - 720)).toBeLessThan(40);
      for (const box of [wordmark, cart]) expect(Math.abs(box.y + box.height / 2 - (nav.y + nav.height / 2))).toBeLessThan(6);
      await expect(page.getByTestId("header-focused").getByText("Return to cart")).toBeVisible();
    } else {
      await expect(full.getByRole("navigation", { name: "Main" })).toBeHidden();
      // Arrow only; the label stays available to assistive tech.
      await expect(page.getByTestId("header-focused").getByRole("link", { name: "Return to cart" })).toBeVisible();
    }
    expect(await noSideScroll(page)).toBe(true);
    await capture("gallery-headers");
  });
});

test.describe("directional fill", () => {
  const fillOf = (page: Page, name: string) =>
    page.getByRole("button", { name }).evaluate((button) => {
      const fill = button.firstElementChild as HTMLElement;
      return {
        duration: getComputedStyle(fill).transitionDuration,
        width: Math.round(fill.getBoundingClientRect().width),
        button: button.clientWidth, // inside the 1px border, where the fill lives
      };
    });

  test("sweeps across the button on keyboard focus", async ({ page }) => {
    await page.goto(GALLERY_URL);
    const before = await fillOf(page, "Add to cart");
    expect(before.duration).toBe("0.24s");
    expect(before.width).toBeLessThan(before.button);
    await page.getByRole("button", { name: "Add to cart" }).focus();
    await expect.poll(async () => (await fillOf(page, "Add to cart")).width).toBe(before.button);
  });

  test("is instant when the visitor prefers reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(GALLERY_URL);
    expect((await fillOf(page, "Add to cart")).duration).toBe("0s");
    await page.getByRole("button", { name: "Add to cart" }).focus();
    const after = await fillOf(page, "Add to cart");
    expect(after.width).toBe(after.button);
  });

  test("never fills a disabled button", async ({ page }) => {
    await page.goto(GALLERY_URL);
    const sold = page.getByRole("button", { name: "Sold out" });
    await sold.hover({ force: true });
    const state = await fillOf(page, "Sold out");
    expect(state.width).toBeLessThan(state.button);
  });
});
