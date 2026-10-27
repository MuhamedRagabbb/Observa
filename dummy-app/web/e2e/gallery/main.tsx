// Component gallery for the browser tests and the visual review (never shipped). It shows each
// shared component in its states, plus the full header layout that later slices will fill in,
// so the approved layout can be checked before every header feature works.
import { StrictMode, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router";
import { AppProviders } from "../../src/shared/AppProviders.tsx";
import "../../src/shared/global.css";
import { BrandMark, Wordmark } from "../../src/shared/ui/Brand.tsx";
import { Button } from "../../src/shared/ui/Button.tsx";
import { Notice } from "../../src/shared/ui/Notice.tsx";
import { QuantityStepper } from "../../src/shared/ui/QuantityStepper.tsx";
import { StatusMarker } from "../../src/shared/ui/StatusMarker.tsx";
import { TextButton, TextLink } from "../../src/shared/ui/TextLink.tsx";
import { displayName } from "../../src/store/catalog.ts";
import { ProductImage } from "../../src/store/ProductImage.tsx";
import { StoreHeader } from "../../src/store/StoreHeader.tsx";
import { PRODUCTS } from "../fixtures/gateway.ts";

const noop = () => undefined;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} style={{ display: "grid", gap: "var(--space-4)", paddingBlock: "var(--space-8)" }}>
      <h2 style={{ fontSize: "var(--text-heading-3)" }}>{title}</h2>
      {children}
    </section>
  );
}

const row = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-6)" } as const;

function Stepper({ start }: { start: number }) {
  const [value, setValue] = useState(start);
  return <QuantityStepper itemName={`Item at ${start}`} value={value} min={1} max={10} onChange={setValue} />;
}

function Components() {
  return (
    <main aria-label="Components" style={{ width: "min(80rem, 100% - 2 * var(--layout-gutter))", marginInline: "auto" }}>
      <Section title="Brand">
        <div style={{ ...row, color: "var(--color-ink)", ["--wordmark-height" as string]: "clamp(1.3rem, 8vw, 3.5rem)" }}>
          <Wordmark />
          <div style={{ width: "3rem", color: "var(--color-glyph-disabled)" }}>
            <BrandMark />
          </div>
        </div>
      </Section>
      <Section title="Buttons">
        <div style={row} data-testid="buttons">
          <Button icon="plus">Add to cart</Button>
          <Button icon="minus" disabled>
            Sold out
          </Button>
          <Button icon="refresh">Check status</Button>
          <Button icon="arrow-right" variant="secondary">
            Shop the collection
          </Button>
        </div>
        <div style={row}>
          <Button icon="arrow-right" size="large">
            Shop the collection
          </Button>
        </div>
        <div style={{ display: "grid", gap: "var(--space-4)", maxWidth: "34rem" }}>
          <Button icon="arrow-right" size="large" block loading>
            Placing order
          </Button>
          <Button icon="arrow-right" size="large" block disabled>
            Proceed to Checkout
          </Button>
        </div>
      </Section>
      <Section title="Links">
        <div style={row}>
          <TextLink to="/" icon="arrow-left">
            Return to cart
          </TextLink>
          <TextButton onClick={noop}>Remove</TextButton>
        </div>
      </Section>
      <Section title="Status">
        <div style={row}>
          <StatusMarker shape="filled" tone="success">
            In stock
          </StatusMarker>
          <StatusMarker shape="hollow" tone="muted">
            Out of stock
          </StatusMarker>
          <StatusMarker shape="filled" tone="ink">
            Confirmed
          </StatusMarker>
          <StatusMarker shape="half" tone="ink">
            Processing
          </StatusMarker>
          <StatusMarker shape="hollow" tone="ink">
            Status unknown
          </StatusMarker>
          <StatusMarker shape="hollow" tone="fault">
            Payment declined
          </StatusMarker>
        </div>
      </Section>
      <Section title="Notices">
        <div style={{ display: "grid", gap: "var(--space-4)", maxWidth: "34rem" }}>
          <Notice icon="info">This is a demo store. No card is charged and nothing is shipped.</Notice>
          <Notice icon="ring" action={<TextButton onClick={noop}>Remove</TextButton>}>
            1 item is out of stock.
          </Notice>
        </div>
      </Section>
      <Section title="Quantity">
        <div style={row}>
          <Stepper start={1} />
          <Stepper start={2} />
          <Stepper start={10} />
          <QuantityStepper itemName="Unavailable item" value={1} min={1} max={10} onChange={noop} disabled />
        </div>
      </Section>
      <Section title="Product images">
        <div
          data-testid="images"
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(9rem, 1fr))", gap: "var(--space-4)" }}
        >
          {[...PRODUCTS, { product_id: "sku-999", name: "Unknown product" }].map((product) => (
            <figure key={product.product_id} style={{ margin: 0 }}>
              <ProductImage productId={product.product_id} priority />
              <figcaption style={{ fontSize: "var(--text-small)", paddingBlock: "var(--space-2)" }}>
                {displayName(product)}
              </figcaption>
            </figure>
          ))}
        </div>
      </Section>
    </main>
  );
}

function Headers() {
  return (
    <div style={{ width: "min(80rem, 100% - 2 * var(--layout-gutter))", marginInline: "auto" }}>
      <div data-testid="header-full">
        <StoreHeader
          links={[
            { to: "/", label: "Home", end: true },
            { to: "/products", label: "Products" },
            { to: "/orders", label: "My Orders" },
          ]}
          search={{ onOpen: noop }}
          cart={{ count: 2, onOpen: noop }}
        />
      </div>
      <div data-testid="header-focused" style={{ marginBlockStart: "var(--space-16)" }}>
        <StoreHeader back={{ to: "/", label: "Return to cart" }} />
      </div>
    </div>
  );
}

const view = new URLSearchParams(location.search).get("view");
const router = createMemoryRouter([{ path: "*", element: view === "headers" ? <Headers /> : <Components /> }], {
  initialEntries: ["/"],
});

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");
createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
