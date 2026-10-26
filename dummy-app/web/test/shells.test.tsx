import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/react";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { storeRoutes } from "../src/store/routes.tsx";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import type { RouteObject } from "react-router";

afterEach(cleanup);

function renderShell(routes: RouteObject[]) {
  const router = createMemoryRouter(routes, { initialEntries: ["/"] });
  return render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

describe("bundle shells", () => {
  it("the storefront shell shows the OBSERVA wordmark and an empty storefront region", () => {
    renderShell(storeRoutes);
    const banner = screen.getByRole("banner");
    expect(within(banner).getByText("OBSERVA")).toBeTruthy();
    expect(within(banner).queryByText("Operator")).toBeNull();
    expect(screen.getByRole("main", { name: "Storefront" })).toBeTruthy();
  });

  it("the operator shell is labelled as the operator surface", () => {
    renderShell(operatorRoutes);
    const banner = screen.getByRole("banner");
    expect(within(banner).getByText("OBSERVA")).toBeTruthy();
    expect(within(banner).getByText("Operator")).toBeTruthy();
    expect(screen.getByRole("main", { name: "Operator" })).toBeTruthy();
  });

  it("neither shell shows sample data", () => {
    renderShell(storeRoutes);
    renderShell(operatorRoutes);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\$\d|ATT-|Healthy|sku-/);
  });
});
