// The OBSERVA wordmark (mark plus letters) and the standalone mark (empty cart).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BrandMark, Wordmark } from "../src/shared/ui/Brand.tsx";

afterEach(cleanup);

describe("Wordmark", () => {
  it("is one image named OBSERVA, drawn as vector paths with no font dependency", () => {
    const { container } = render(<Wordmark />);
    const image = screen.getByRole("img", { name: "OBSERVA" });
    expect(image.tagName.toLowerCase()).toBe("svg");
    expect(container.querySelectorAll("path").length).toBeGreaterThanOrEqual(8);
    expect(container.querySelector("text")).toBeNull();
  });
});

describe("BrandMark", () => {
  it("is decorative by default", () => {
    const { container } = render(<BrandMark />);
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByRole("img")).toBeNull();
  });
});
