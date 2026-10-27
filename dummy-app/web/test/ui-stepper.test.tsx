// The cart's quantity stepper: minus, value, plus (Cart drawer artboards). Limits 1-10 match
// the backend's per-line quantity bounds; the cart (P4-C) passes them in.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QuantityStepper } from "../src/shared/ui/QuantityStepper.tsx";

afterEach(cleanup);

const renderStepper = (value: number, onChange = vi.fn()) => {
  render(<QuantityStepper itemName="Coffee Mug" value={value} min={1} max={10} onChange={onChange} />);
  return onChange;
};

describe("QuantityStepper", () => {
  it("names its buttons and group after the item", () => {
    renderStepper(2);
    expect(screen.getByRole("group", { name: "Quantity of Coffee Mug" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decrease quantity of Coffee Mug" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Increase quantity of Coffee Mug" })).toBeTruthy();
  });

  it("shows the value and announces changes to it", () => {
    renderStepper(2);
    const value = screen.getByRole("status");
    expect(value.textContent).toBe("Quantity 2");
    expect(value.getAttribute("aria-live")).toBe("polite");
  });

  it("asks for one more or one less", () => {
    const onChange = renderStepper(2);
    fireEvent.click(screen.getByRole("button", { name: /Increase/ }));
    fireEvent.click(screen.getByRole("button", { name: /Decrease/ }));
    expect(onChange.mock.calls).toEqual([[3], [1]]);
  });

  it("cannot go below the minimum, and keeps focus on the button that reached it", () => {
    const onChange = renderStepper(1);
    const minus = screen.getByRole("button", { name: /Decrease/ }) as HTMLButtonElement;
    expect(minus.getAttribute("aria-disabled")).toBe("true");
    minus.focus();
    expect(document.activeElement).toBe(minus);
    fireEvent.click(minus);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("cannot go above the maximum", () => {
    const onChange = renderStepper(10);
    const plus = screen.getByRole("button", { name: /Increase/ }) as HTMLButtonElement;
    expect(plus.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(plus);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("brings a quantity above the maximum straight back into range", () => {
    // A line at 5 when stock (and so the maximum) drops to 3.
    const onChange = vi.fn();
    render(<QuantityStepper itemName="Coffee Mug" value={5} min={1} max={3} onChange={onChange} />);
    const minus = screen.getByRole("button", { name: /Decrease/ });
    expect(minus.getAttribute("aria-disabled")).toBeNull();
    expect(screen.getByRole("button", { name: /Increase/ }).getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(minus);
    expect(onChange.mock.calls).toEqual([[3]]);
  });

  it("can be disabled as a whole, for an unavailable item", () => {
    render(<QuantityStepper itemName="Limited Poster" value={1} min={1} max={10} onChange={vi.fn()} disabled />);
    for (const button of screen.getAllByRole("button")) expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
