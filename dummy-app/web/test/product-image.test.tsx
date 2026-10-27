// Product images hold the artboard frame (4:5 portrait) before the file loads, so nothing
// shifts, and fall back to a neutral tile for a product without an image.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { productImage } from "../src/store/catalog.ts";
import { ProductImage } from "../src/store/ProductImage.tsx";

afterEach(cleanup);

describe("ProductImage", () => {
  it("shows the product's file inside a frame that already has the 4:5 ratio", () => {
    const { container } = render(<ProductImage productId="sku-002" />);
    const frame = container.firstElementChild as HTMLElement;
    expect(frame.style.aspectRatio).toBe("4 / 5");
    const img = container.querySelector("img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(productImage("sku-002").src);
    expect(img.getAttribute("width")).toBe("400");
    expect(img.getAttribute("height")).toBe("500");
  });

  it("is decorative by default, because the product name sits beside it", () => {
    const { container } = render(<ProductImage productId="sku-002" />);
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("takes alternative text when it stands alone", () => {
    render(<ProductImage productId="sku-002" alt="Coffee Mug" />);
    expect(screen.getByRole("img", { name: "Coffee Mug" })).toBeTruthy();
  });

  it("loads lazily unless it is above the fold", () => {
    const { container, rerender } = render(<ProductImage productId="sku-001" />);
    expect(container.querySelector("img")?.getAttribute("loading")).toBe("lazy");
    rerender(<ProductImage productId="sku-001" priority />);
    expect(container.querySelector("img")?.getAttribute("loading")).toBe("eager");
  });

  it("shows a neutral tile of the same ratio for a product without an image", () => {
    const { container } = render(<ProductImage productId="sku-999" />);
    const frame = container.firstElementChild as HTMLElement;
    expect(frame.style.aspectRatio).toBe("4 / 5");
    expect(container.querySelector("img")).toBeNull();
    expect(frame.dataset.placeholder).toBe("true");
  });
});
