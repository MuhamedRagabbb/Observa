// The directional-fill button: an icon block plus a label (artboards: "Add to cart",
// "Shop the collection", "Place Order", "Check status", "Sold out").
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button, ButtonLink } from "../src/shared/ui/Button.tsx";

afterEach(cleanup);

describe("Button", () => {
  it("is a real button named by its label, with the icon hidden from assistive tech", () => {
    const onClick = vi.fn();
    render(
      <Button icon="plus" onClick={onClick}>
        Add to cart
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Add to cart" });
    expect(button.getAttribute("type")).toBe("button");
    expect(button.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("can submit a form when asked", () => {
    render(
      <Button icon="arrow-right" type="submit">
        Place order
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Place order" }).getAttribute("type")).toBe("submit");
  });

  it("when disabled, cannot be pressed", () => {
    const onClick = vi.fn();
    render(
      <Button icon="minus" disabled onClick={onClick}>
        Sold out
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Sold out" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("while loading, stays focusable but reports busy and ignores presses", () => {
    const onClick = vi.fn();
    render(
      <Button icon="arrow-right" loading onClick={onClick}>
        Placing order
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Placing order" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    button.focus();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("while loading, does not submit its form", () => {
    const onSubmit = vi.fn((event: SubmitEvent) => event.preventDefault());
    render(
      <form onSubmit={(event) => onSubmit(event.nativeEvent as SubmitEvent)}>
        <Button icon="arrow-right" type="submit" loading>
          Placing order
        </Button>
      </form>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Placing order" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("can be unavailable yet keep focus, for a control that just reached a limit", () => {
    const onClick = vi.fn();
    render(
      <Button icon="plus" unavailable onClick={onClick}>
        10 in cart
      </Button>,
    );
    const button = screen.getByRole("button", { name: "10 in cart" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-busy")).toBeNull();
    button.focus();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(button);
  });

  it("has primary and secondary variants", () => {
    render(
      <>
        <Button icon="arrow-right">Primary</Button>
        <Button icon="arrow-right" variant="secondary">
          Secondary
        </Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Primary" }).dataset.variant).toBe("primary");
    expect(screen.getByRole("button", { name: "Secondary" }).dataset.variant).toBe("secondary");
  });
});

describe("ButtonLink", () => {
  it("is a link, not a button, when it navigates", () => {
    const router = createMemoryRouter(
      [{ path: "/", element: <ButtonLink to="/orders" icon="arrow-right">Shop the collection</ButtonLink> }],
      { initialEntries: ["/"] },
    );
    render(<RouterProvider router={router} />);
    const link = screen.getByRole("link", { name: "Shop the collection" });
    expect(link.getAttribute("href")).toBe("/orders");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
