// Status markers (collection stock, order status) and notice banners (cart, checkout).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Notice } from "../src/shared/ui/Notice.tsx";
import { StatusMarker } from "../src/shared/ui/StatusMarker.tsx";
import { TextButton } from "../src/shared/ui/TextLink.tsx";

afterEach(cleanup);

describe("StatusMarker", () => {
  it("always states the status in words; the dot is decoration", () => {
    const { container } = render(
      <StatusMarker shape="hollow" tone="muted">
        Out of stock
      </StatusMarker>,
    );
    expect(screen.getByText("Out of stock")).toBeTruthy();
    const dot = container.querySelector("[data-shape]");
    expect(dot?.getAttribute("aria-hidden")).toBe("true");
    expect(dot?.getAttribute("data-shape")).toBe("hollow");
    expect(dot?.getAttribute("data-tone")).toBe("muted");
  });
});

describe("Notice", () => {
  it("is static by default, so it is read in place and not announced", () => {
    render(<Notice icon="info">This is a demo store. No card is charged and nothing is shipped.</Notice>);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText(/No card is charged/)).toBeTruthy();
  });

  it("announces itself by adding its message to a live region that already exists", async () => {
    // Screen readers announce changes inside an existing live region, not a region that
    // arrives with its text, so the message must land after the region is in the page.
    const host = document.createElement("div");
    document.body.append(host);
    const added: string[] = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const target = record.target instanceof Element ? record.target : record.target.parentElement;
        if (target?.closest("[role='status']")) {
          for (const node of record.addedNodes) added.push(node.textContent ?? "");
        }
      }
    });
    observer.observe(host, { childList: true, subtree: true });
    render(
      <Notice icon="ring" live>
        1 item is out of stock.
      </Notice>,
      { container: host },
    );
    await screen.findByText("1 item is out of stock.");
    observer.disconnect();
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
    expect(added.some((text) => text.includes("1 item is out of stock."))).toBe(true);
    host.remove();
  });

  it("holds an optional action beside its message", () => {
    render(
      <Notice icon="ring" action={<TextButton aria-label="Remove unavailable items">Remove</TextButton>}>
        1 item is out of stock.
      </Notice>,
    );
    expect(screen.getByRole("button", { name: "Remove unavailable items" })).toBeTruthy();
  });
});
