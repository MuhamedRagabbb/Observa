// P4-G: product search against the production build, with /api/products answered by the seeded
// catalogue. Runs at 1440, 390 and 375: the header's Search opens a field above the collection,
// the collection is filtered by display name, and everything works from the keyboard.
import type { Locator, Page } from "@playwright/test";
import { PRODUCTS } from "./fixtures/gateway.ts";
import { STORE_URL, expect, test, type MockApi } from "./support/harness.ts";

const isDesktop = (projectName: string) => projectName === "desktop-1440";
const collection = (page: Page) => page.getByRole("region", { name: "The Collection" });
const cardNames = (page: Page) => collection(page).getByRole("heading", { level: 3 }).allTextContents();
const searchButton = (page: Page) => page.getByRole("banner").getByRole("button", { name: "Search" });
const field = (page: Page) => page.getByRole("searchbox", { name: "Search products" });
const ALL = ["Journal / Notebook", "Coffee Mug", "Pen Set", "Limited Poster / Art Print", "Organizing Labels"];
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

function serve(api: MockApi) {
  api.onGateway("GET", "/api/products", () => ({ status: 200, body: PRODUCTS }));
  api.onGateway("GET", "/api/orders", () => ({ status: 200, body: [] }));
}

/** Image tiles are lazy: scroll the collection through so every file has loaded before a capture. */
async function loadImages(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  for (const image of await collection(page).locator("img").all()) {
    await image.scrollIntoViewIfNeeded();
    await expect(image).toHaveJSProperty("complete", true);
  }
}

const inView = async (locator: Locator) =>
  locator.evaluate((element) => {
    const { top, bottom } = element.getBoundingClientRect();
    return top >= 0 && bottom <= window.innerHeight;
  });

test.describe("search", () => {
  test.beforeEach(({ api }) => serve(api));

  test("Search opens a field above the collection, brings the collection into view and takes focus", async ({ page }) => {
    await page.goto(STORE_URL);
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await expect(searchButton(page)).toHaveAttribute("aria-expanded", "false");
    await searchButton(page).click();
    await expect(field(page)).toBeFocused();
    await expect(searchButton(page)).toHaveAttribute("aria-expanded", "true");
    // The field is above the grid, in view, with the first results beneath it.
    await expect.poll(() => inView(field(page))).toBe(true);
    expect(await noSideScroll(page)).toBe(true);
  });

  test("typing filters the collection, ignoring case; results are counted and announced", async ({ page, capture }) => {
    await page.goto(STORE_URL);
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await searchButton(page).click();
    await field(page).fill("PEN");
    expect(await cardNames(page)).toEqual(["Pen Set"]);
    await expect(collection(page).getByText("1 of 5 objects", { exact: true })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "1 of 5 objects match “PEN”" })).toBeAttached();
    await field(page).fill("t");
    expect(await cardNames(page)).toEqual(["Journal / Notebook", "Pen Set", "Limited Poster / Art Print"]);
    await loadImages(page);
    await field(page).scrollIntoViewIfNeeded();
    await capture("search-results", collection(page));
    expect(await noSideScroll(page)).toBe(true);
  });

  test("no results: says so, offers Clear search, and Clear brings everything back", async ({ page, capture }) => {
    await page.goto(STORE_URL);
    await searchButton(page).click();
    await field(page).fill("zzz");
    await expect(collection(page).getByText("No objects match “zzz”.")).toBeVisible();
    expect(await cardNames(page)).toEqual([]);
    await page.evaluate(() => document.fonts.ready);
    await capture("search-no-results", collection(page));
    await collection(page).getByRole("button", { name: "Clear search" }).click();
    await expect(field(page)).toHaveValue("");
    await expect(field(page)).toBeFocused();
    expect(await cardNames(page)).toEqual(ALL);
  });

  test("the keyboard does all of it: Tab to Search, Enter opens, Escape closes and returns focus", async ({ page }) => {
    await page.goto(STORE_URL);
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await searchButton(page).focus();
    await page.keyboard.press("Enter");
    await expect(field(page)).toBeFocused();
    await page.keyboard.type("mug");
    expect(await cardNames(page)).toEqual(["Coffee Mug"]);
    // Tab leaves the field for Clear, then Close; the cart action of the result follows.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Clear" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Close search" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Add to cart: Coffee Mug" })).toBeFocused();
    await field(page).focus();
    await page.keyboard.press("Escape");
    await expect(field(page)).toHaveCount(0);
    await expect(searchButton(page)).toBeFocused();
    await expect(searchButton(page)).toHaveAttribute("aria-expanded", "false");
    expect(await cardNames(page)).toEqual(ALL);
  });

  test("a visible Close button ends the search too (no Escape key on a phone)", async ({ page }) => {
    await page.goto(STORE_URL);
    await searchButton(page).click();
    await field(page).fill("pen");
    await page.getByRole("button", { name: "Close search" }).click();
    await expect(field(page)).toHaveCount(0);
    await expect(searchButton(page)).toBeFocused();
    expect(await cardNames(page)).toEqual(ALL);
  });

  test("a very long query wraps in the no-results message instead of widening the page", async ({ page }) => {
    await page.goto(STORE_URL);
    await searchButton(page).click();
    await field(page).fill("x".repeat(80));
    await expect(collection(page).getByText(/No objects match/)).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
  });

  test("a filtered product can still be added to the cart", async ({ page }) => {
    await page.goto(STORE_URL);
    await searchButton(page).click();
    await field(page).fill("labels");
    await collection(page).getByRole("button", { name: "Add to cart: Organizing Labels" }).click();
    await expect(page.getByRole("banner").getByRole("button", { name: "Cart, 1 item" })).toBeVisible();
  });

  test("from My Orders, Search goes to the collection with the field open; Back does not reopen it", async ({ page }) => {
    await page.goto(`${STORE_URL}orders`);
    await expect(page.getByRole("heading", { level: 1, name: "My Orders" })).toBeVisible();
    await searchButton(page).click();
    await expect(page).toHaveURL(/\/ui\/next\/?$/);
    await expect(field(page)).toBeFocused();
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await expect.poll(() => inView(field(page))).toBe(true);
    // The request was spent on arrival: Back and Forward do not reopen the field.
    await page.keyboard.press("Escape");
    await expect(field(page)).toHaveCount(0);
    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: "My Orders" })).toBeVisible();
    await page.goForward();
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await expect(field(page)).toHaveCount(0);
  });

  test("leaving for another page ends the search", async ({ page }) => {
    await page.goto(STORE_URL);
    await searchButton(page).click();
    await field(page).fill("pen");
    await page.getByRole("banner").getByRole("link", { name: "My Orders" }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "My Orders" })).toBeVisible();
    await page.getByRole("banner").getByRole("link", { name: "OBSERVA" }).click();
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await expect(field(page)).toHaveCount(0);
  });

  test("on phones the icon-only Search button is a full touch target; the checkout header has none", async ({ page }, info) => {
    await page.goto(STORE_URL);
    if (!isDesktop(info.project.name)) {
      const box = await searchButton(page).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    }
    await page.goto(`${STORE_URL}checkout`);
    await expect(searchButton(page)).toHaveCount(0);
  });
});
