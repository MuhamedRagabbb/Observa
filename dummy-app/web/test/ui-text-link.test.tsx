// Underlined text actions (artboards: "Return to cart", "Remove", "View").
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TextButton, TextLink } from "../src/shared/ui/TextLink.tsx";

afterEach(cleanup);

describe("TextLink", () => {
  it("navigates as a link, with an optional leading icon hidden from assistive tech", () => {
    const router = createMemoryRouter(
      [{ path: "/", element: <TextLink to="/checkout" icon="arrow-left">Return to cart</TextLink> }],
      { initialEntries: ["/"] },
    );
    render(<RouterProvider router={router} />);
    const link = screen.getByRole("link", { name: "Return to cart" });
    expect(link.getAttribute("href")).toBe("/checkout");
    expect(link.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("TextButton", () => {
  it("is a button when it acts on the page instead of navigating", () => {
    const onClick = vi.fn();
    render(<TextButton onClick={onClick}>Remove</TextButton>);
    const button = screen.getByRole("button", { name: "Remove" });
    expect(button.getAttribute("type")).toBe("button");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("can carry a fuller accessible name than its visible label", () => {
    render(<TextButton aria-label="Remove Coffee Mug">Remove</TextButton>);
    expect(screen.getByRole("button", { name: "Remove Coffee Mug" })).toBeTruthy();
  });
});
