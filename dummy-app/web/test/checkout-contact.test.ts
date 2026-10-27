// @vitest-environment node
// Contact validation, copied from the backend (services/shared/checkout.py Contact): the form
// never sends what the Gateway would reject with 422, which would leave the attempt unknown.
import { describe, expect, it } from "vitest";
import { validateContact } from "../src/store/checkout/contact.ts";

const ok = { name: "Demo Name", email: "demo@example.com" };

describe("name", () => {
  it("accepts 1 to 80 characters, counted as the backend counts them", () => {
    expect(validateContact(ok)).toEqual({});
    expect(validateContact({ ...ok, name: "A" })).toEqual({});
    expect(validateContact({ ...ok, name: "é".repeat(80) })).toEqual({});
    expect(validateContact({ ...ok, name: "😀".repeat(80) })).toEqual({});
    expect(validateContact({ ...ok, name: "x".repeat(81) }).name).toBe("Use 80 characters or fewer.");
  });

  it("asks for a name when it is empty or only spaces", () => {
    expect(validateContact({ ...ok, name: "" }).name).toBe("Enter a name.");
    expect(validateContact({ ...ok, name: "   " }).name).toBe("Enter a name.");
  });

  it("rejects control characters, as the backend does", () => {
    for (const name of ["Demo\nName", "Demo\tName", "Demo\u0000", "Demo\u007f"]) {
      expect(validateContact({ ...ok, name }).name).toBe("Remove line breaks and other hidden characters.");
    }
  });
});

describe("email", () => {
  it.each(["demo@example.com", "a.b+c@sub.example.co", "x@y.z"])("accepts %s", (email) => {
    expect(validateContact({ ...ok, email })).toEqual({});
  });

  it.each(["", "demo", "demo@", "@example.com", "demo@example", "demo@@example.com", "de mo@example.com", "demo@exa mple.com", "demo@.example.com", "demo@example..com", "demo@example.com."])(
    "rejects %j",
    (email) => {
      expect(validateContact({ ...ok, email }).email).toBe("Enter an email like demo@example.com.");
    },
  );

  it("rejects every character the backend counts as whitespace, such as U+0085 and U+00A0", () => {
    for (const email of ["demo\u0085@example.com", "demo@exa\u00a0mple.com", "demo@example.\u2028com"]) {
      expect(validateContact({ ...ok, email }).email).toBe("Enter an email like demo@example.com.");
    }
  });

  it("rejects more than 254 characters", () => {
    const email = `${"a".repeat(243)}@example.com`;
    expect(email).toHaveLength(255);
    expect(validateContact({ ...ok, email }).email).toBe("Enter an email like demo@example.com.");
  });
});
