// @vitest-environment jsdom
// The anonymous customer ID. Attempt references and the unconfirmed-attempt records live in the
// IndexedDB gate: see checkout-gate.test.ts.
import { afterEach, describe, expect, it } from "vitest";
import { customerId } from "../src/store/checkout/attempts.ts";

afterEach(() => localStorage.clear());

describe("customer ID", () => {
  it("is one anonymous web- ID per browser, in the form the backend accepts", () => {
    const id = customerId();
    expect(id).toMatch(/^web-[0-9a-z]{22}$/);
    expect(id).toMatch(/^[a-zA-Z0-9_-]{1,64}$/);
    expect(customerId()).toBe(id);
  });

  it("replaces a stored value the backend would reject", () => {
    localStorage.setItem("observa.customer", "bad id!");
    expect(customerId()).toMatch(/^web-[0-9a-z]{22}$/);
  });
});
