// The black count circle beside "Cart" (Hero and My Orders artboards).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CountBadge, countLabel } from "../src/shared/ui/CountBadge.tsx";

afterEach(cleanup);

describe("CountBadge", () => {
  it("is the number only, hidden from assistive tech (its owner names the count in words)", () => {
    const { container } = render(<CountBadge count={2} />);
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe("true");
    expect(container.textContent).toBe("2");
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("countLabel", () => {
  it("words a count with the right noun", () => {
    expect(countLabel(1, ["item", "items"])).toBe("1 item");
    expect(countLabel(0, ["item", "items"])).toBe("0 items");
    expect(countLabel(3, ["item", "items"])).toBe("3 items");
  });
});
