// P4-B: the storefront home (hero and The Collection) against the production build, with
// /api/products answered by the seeded catalogue or a failure. Runs at 1440, 390 and 375.
import type { Locator, Page } from "@playwright/test";
import { PRODUCTS } from "./fixtures/gateway.ts";
import { STORE_URL, expect, test, type MockApi } from "./support/harness.ts";

const isDesktop = (projectName: string) => projectName === "desktop-1440";
const collection = (page: Page) => page.getByRole("region", { name: "The Collection" });
const cards = (page: Page) => collection(page).getByRole("listitem");
const box = async (locator: Locator) => {
  const value = await locator.boundingBox();
  if (!value) throw new Error("element is not laid out");
  return value;
};
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const serveProducts = (api: MockApi, body: unknown = PRODUCTS, status = 200) =>
  api.onGateway("GET", "/api/products", () => ({ status, body }));
/** Image tiles are lazy below the fold: scroll through so every file has loaded before a capture. */
async function loadAllImages(page: Page) {
  for (const image of await collection(page).locator("img").all()) {
    await image.scrollIntoViewIfNeeded();
    await expect(image).toHaveJSProperty("complete", true);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

test.describe("hero", () => {
  test("opens with the headline, the lede and the way to the collection", async ({ page, api, capture }, info) => {
    serveProducts(api);
    await page.goto(STORE_URL);
    const headline = page.getByRole("heading", { level: 1, name: "Quiet objects, closely observed." });
    const cta = page.getByRole("link", { name: "Shop the collection" });
    await expect(headline).toBeVisible();
    await expect(cta).toHaveAttribute("href", "/ui/next#collection"); // the server redirects /ui/next to /ui/next/
    // "observed." is set in the display serif's true italic, not a slanted upright.
    const italic = await headline.locator("em").evaluate((em) => {
      const style = getComputedStyle(em);
      return { style: style.fontStyle, family: style.fontFamily, loaded: document.fonts.check(`italic 40px ${style.fontFamily}`) };
    });
    expect(italic.style).toBe("italic");
    expect(italic.loaded).toBe(true);
    if (isDesktop(info.project.name)) {
      // Artboard: copy on the left, the still life on the right, both in the first screen.
      const art = await box(page.locator("img[src*='hero']"));
      expect(art.x).toBeGreaterThan((await box(headline)).x + (await box(headline)).width - 40);
      expect((await box(cta)).y + (await box(cta)).height).toBeLessThan(900);
    }
    expect(await noSideScroll(page)).toBe(true);
    await page.evaluate(() => document.fonts.ready);
    await capture("home-hero", "viewport");
  });

  test("Shop the collection moves keyboard focus to the collection", async ({ page, api }) => {
    serveProducts(api);
    await page.goto(STORE_URL);
    await page.getByRole("link", { name: "Shop the collection" }).focus();
    await page.keyboard.press("Enter");
    const heading = page.getByRole("heading", { level: 2, name: "The Collection" });
    await expect(heading).toBeFocused();
    await expect(heading).toBeInViewport();
    await expect(page).toHaveURL(/\/ui\/next\/?#collection$/);
  });

  test("the header's Products link leads to the collection too", async ({ page, api }, info) => {
    test.skip(!isDesktop(info.project.name), "the centred nav shows only on wide screens");
    serveProducts(api);
    await page.goto(STORE_URL);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Products" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "The Collection" })).toBeFocused();
  });
});

test.describe("the collection", () => {
  test("shows the five real products with API prices and stock", async ({ page, api, capture }) => {
    serveProducts(api);
    await page.goto(STORE_URL);
    await expect(cards(page)).toHaveCount(5);
    await expect(collection(page).getByText("5 objects")).toBeVisible();
    const rows = await cards(page).evaluateAll((items) =>
      items.map((item) => [item.querySelector("h3")?.textContent, item.querySelector("p")?.textContent]),
    );
    expect(rows).toEqual([
      ["Journal / Notebook", "$12.00"],
      ["Coffee Mug", "$18.00"],
      ["Pen Set", "$6.00"],
      ["Limited Poster / Art Print", "$9.00"],
      ["Organizing Labels", "$3.00"],
    ]);
    await expect(cards(page).nth(3).getByText("Out of stock")).toBeVisible();
    await expect(collection(page).getByText("In stock")).toHaveCount(4);
    // One cart action per card (P4-C): Add to cart, or Sold out for the product with no stock.
    await expect(collection(page).getByRole("button", { name: /^Add to cart: / })).toHaveCount(4);
    await expect(collection(page).getByRole("button", { name: /^Sold out: / })).toBeDisabled();
    expect(await noSideScroll(page)).toBe(true);
    await loadAllImages(page);
    await capture("collection", collection(page));
  });

  test("keeps the artboards' asymmetric layout", async ({ page, api }, info) => {
    serveProducts(api);
    await page.goto(STORE_URL);
    await expect(cards(page)).toHaveCount(5);
    const tiles = await Promise.all((await cards(page).all()).map((card) => box(card.locator("img, div").first())));
    const [one, two, three, four, five] = tiles as [typeof tiles[0], typeof tiles[0], typeof tiles[0], typeof tiles[0], typeof tiles[0]];
    if (isDesktop(info.project.name)) {
      // Row one: a 3:2 pair, the second tile dropped. Row two: three equal tiles, staggered.
      expect(one.width / two.width).toBeGreaterThan(1.4);
      expect(one.width / two.width).toBeLessThan(1.6);
      expect(two.y - one.y).toBeGreaterThan(two.width * 0.3);
      expect(Math.abs(three.width - four.width)).toBeLessThan(2);
      expect(Math.abs(four.width - five.width)).toBeLessThan(2);
      expect(four.y).toBeGreaterThan(three.y + 40);
      expect(five.y).toBeGreaterThan(three.y);
      expect(five.y).toBeLessThan(four.y);
    } else {
      // Phone: the first tile spans the width, then two columns.
      expect(one.width).toBeGreaterThan(two.width * 1.9);
      expect(Math.abs(two.y - three.y)).toBeLessThan(2);
      expect(three.x).toBeGreaterThan(two.x + two.width);
      expect(Math.abs(four.y - five.y)).toBeLessThan(2);
    }
    for (const tile of tiles) expect(Math.round((tile.height / tile.width) * 100) / 100).toBe(1.25);
  });

  test("says so while the products load", async ({ page, api, capture }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    api.onGateway("GET", "/api/products", async () => {
      await held;
      return { status: 200, body: PRODUCTS };
    });
    await page.goto(STORE_URL);
    await expect(collection(page)).toHaveAttribute("aria-busy", "true");
    await expect(collection(page).getByRole("status")).toHaveText("Loading the collection…");
    await expect(collection(page).getByText(/objects$/)).toHaveCount(0);
    await capture("collection-loading", collection(page));
    release();
    await expect(cards(page)).toHaveCount(5);
    await expect(collection(page)).not.toHaveAttribute("aria-busy", "true");
  });

  test("explains a failure and recovers on Try again", async ({ page, api, capture }) => {
    let failing = true;
    api.onGateway("GET", "/api/products", () =>
      failing
        ? { status: 503, body: { error_code: "dependency_unavailable", message: "Inventory unavailable" } }
        : { status: 200, body: PRODUCTS },
    );
    await page.goto(STORE_URL);
    const alert = collection(page).getByRole("alert");
    await expect(alert).toContainText("The collection didn’t load.");
    await capture("collection-error", collection(page));
    failing = false;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(cards(page)).toHaveCount(5);
    const requests = api.calls.filter((call) => call.path === "/api/products");
    expect(requests).toHaveLength(2);
    for (const request of requests) expect(request.headers["x-request-id"]).toMatch(/^web-[0-9a-f-]{36}$/);
  });

  test("says so when there are no products", async ({ page, api, capture }) => {
    serveProducts(api, []);
    await page.goto(STORE_URL);
    await expect(collection(page).getByText("No objects are available right now.")).toBeVisible();
    await expect(collection(page).getByRole("list")).toHaveCount(0);
    await capture("collection-empty", collection(page));
  });
});
