// Storefront artboard-to-test coverage. One entry per UI state. Every state is screenshotted at
// all three review widths:
// - approved: an artboard exists for that width (file in docs/design/artboards/, local only);
// - derived: no artboard for that width. The review page shows `reference` for that width if
//   set (an approved artboard of another state, such as the mobile cart sheet), otherwise the
//   state's own nearest approved width.
// Used by the Playwright capture helper and by the side-by-side review page.

export const VIEWPORTS = {
  "desktop-1440": { width: 1440, height: 900 },
  "mobile-390": { width: 390, height: 844 },
  "mobile-375": { width: 375, height: 812 },
} as const;
export type Viewport = keyof typeof VIEWPORTS;

export const SLICES = ["P4-A", "P4-B", "P4-C", "P4-D", "P4-E", "P4-F", "P4-G"] as const;
export type Slice = (typeof SLICES)[number];

export type CoverageState = {
  id: string;
  slice: Slice;
  /** What the screen is, in a few words. */
  screen: string;
  /** Artboard file per width that has one. */
  approved: Partial<Record<Viewport, string>>;
  /** Widths with no artboard. */
  derived: Viewport[];
  /** For derived widths: the approved artboard (of any state) to review against. */
  reference?: Partial<Record<Viewport, string>>;
  note?: string;
};

const ALL: Viewport[] = ["desktop-1440", "mobile-390", "mobile-375"];
const MOBILE: Viewport[] = ["mobile-390", "mobile-375"];

const A = {
  hero: "Hero background — 16 9 desktop.png",
  collection: "Product Collection — 1440 desktop.png",
  collectionMobile: "Product Collection — 390 mobile.png",
  cartSheet: "Cart sheet — 375 mobile.png",
  checkout: "Checkout — 1440 desktop.png",
  checkoutMobile: "Checkout — 375 mobile.png",
  processing: "Outcome 1 — Processing.png",
  processingMobile: "Mobile — Processing.png",
  declined: "Outcome 3a — Payment failed, retryable.png",
  declinedMobile: "Mobile — Payment failed.png",
  notPlaced: "Outcome 3b — Failed, not retryable.png",
  unknown: "Outcome 4 — Status unknown.png",
  unknownMobile: "Mobile — Status unknown.png",
  ordersList: "My Orders — list, 1440 desktop.png",
  ordersListMobile: "My Orders — 375 mobile list.png",
  detailConfirmed: "My Orders — order detail, confirmed.png",
  detailUnknown: "My Orders — order detail, status unknown.png",
  detailUnknownMobile: "My Orders — 375 mobile, status unknown.png",
} as const;

/** The same reference artboard at both mobile widths. */
const mobileRef = (file: string) => ({ "mobile-390": file, "mobile-375": file });
/** Reference artboards for a state with no artboard of its own. */
const allRef = (desktop: string, mobile: string) => ({ "desktop-1440": desktop, ...mobileRef(mobile) });

export const COVERAGE: CoverageState[] = [
  // P4-B: home and collection (P4-A's header and components appear in these captures)
  {
    id: "home-hero",
    slice: "P4-B",
    screen: "Home: header and hero",
    approved: { "desktop-1440": A.hero },
    derived: MOBILE,
    reference: mobileRef(A.collectionMobile),
    note: "Audit item 1: no mobile hero artboard; mobile header and hero are derived.",
  },
  {
    id: "collection",
    slice: "P4-B",
    screen: "The Collection, 5 real products, sku-004 sold out",
    approved: { "desktop-1440": A.collection, "mobile-390": A.collectionMobile },
    derived: ["mobile-375"],
  },
  {
    id: "collection-loading",
    slice: "P4-B",
    screen: "Collection while products load",
    approved: {},
    derived: ALL,
    reference: allRef(A.collection, A.collectionMobile),
  },
  {
    id: "collection-error",
    slice: "P4-B",
    screen: "Products failed to load",
    approved: {},
    derived: ALL,
    reference: allRef(A.collection, A.collectionMobile),
  },
  {
    id: "collection-empty",
    slice: "P4-B",
    screen: "The API returned no products",
    approved: {},
    derived: ALL,
    reference: allRef(A.collection, A.collectionMobile),
  },

  // P4-C: cart
  {
    id: "cart-ready",
    slice: "P4-C",
    screen: "Cart drawer, ready to check out",
    approved: { "desktop-1440": "Cart drawer — desktop, ready to check out.png" },
    derived: MOBILE,
    reference: mobileRef(A.cartSheet),
  },
  {
    id: "cart-unavailable",
    slice: "P4-C",
    screen: "Cart with an out-of-stock line, checkout disabled",
    approved: { "desktop-1440": "Cart drawer — desktop, with unavailable item.png", "mobile-375": A.cartSheet },
    derived: ["mobile-390"],
  },
  {
    id: "cart-empty",
    slice: "P4-C",
    screen: "Empty cart",
    approved: { "desktop-1440": "Cart drawer — desktop, empty.png" },
    derived: MOBILE,
    reference: mobileRef(A.cartSheet),
  },
  {
    id: "cart-loading",
    slice: "P4-C",
    screen: "Cart while the quote loads",
    approved: { "desktop-1440": "Cart drawer — desktop, loading.png" },
    derived: MOBILE,
    reference: mobileRef(A.cartSheet),
  },
  {
    id: "cart-error",
    slice: "P4-C",
    screen: "Quote failed, Try again",
    approved: { "desktop-1440": "Cart drawer — desktop, error.png" },
    derived: MOBILE,
    reference: mobileRef(A.cartSheet),
  },

  // P4-D: checkout and definite outcomes
  {
    id: "checkout-form",
    slice: "P4-D",
    screen: "Checkout form with order summary",
    approved: { "desktop-1440": A.checkout, "mobile-375": A.checkoutMobile },
    derived: ["mobile-390"],
  },
  {
    id: "checkout-form-invalid",
    slice: "P4-D",
    screen: "Contact fields rejected by the backend's own rules, nothing sent",
    approved: {},
    derived: ALL,
    reference: allRef(A.checkout, A.checkoutMobile),
  },
  {
    id: "outcome-processing",
    slice: "P4-D",
    screen: "Placing order (submitted, or 202 while polling)",
    approved: { "desktop-1440": A.processing, "mobile-375": A.processingMobile },
    derived: ["mobile-390"],
  },
  {
    id: "outcome-success",
    slice: "P4-D",
    screen: "Order confirmed with the real order number",
    approved: { "desktop-1440": "Outcome 2 — Success.png", "mobile-375": "Mobile — Success.png" },
    derived: ["mobile-390"],
  },
  {
    id: "outcome-payment-declined",
    slice: "P4-D",
    screen: "402 payment declined (result in details), safe to retry with a new reference",
    approved: { "desktop-1440": A.declined, "mobile-375": A.declinedMobile },
    derived: ["mobile-390"],
  },
  {
    id: "outcome-payment-unavailable",
    slice: "P4-D",
    screen: "503 payment unavailable (result in details), nothing charged, safe to retry",
    approved: {},
    derived: ALL,
    reference: allRef(A.declined, A.declinedMobile),
    note: "Copy variant of 3a.",
  },
  {
    id: "outcome-not-placed",
    slice: "P4-D",
    screen: "409 insufficient stock (result in details), unavailable lines marked, Review cart",
    approved: { "desktop-1440": A.notPlaced },
    derived: MOBILE,
    reference: mobileRef(A.declinedMobile),
  },
  {
    id: "outcome-failed",
    slice: "P4-D",
    screen: "503/500 checkout failed (result in details; retryable flag from the result)",
    approved: {},
    derived: ALL,
    reference: allRef(A.notPlaced, A.declinedMobile),
    note: "Copy variant of 3b.",
  },

  // P4-E: unknown outcome and recovery
  {
    id: "outcome-unknown",
    slice: "P4-E",
    screen: "No result for this reference (network, 502/504, or an error without details)",
    approved: { "desktop-1440": A.unknown, "mobile-375": A.unknownMobile },
    derived: ["mobile-390"],
  },
  {
    id: "outcome-still-processing",
    slice: "P4-E",
    screen: "Polling gave up after about 60 s: still processing, Check status again",
    approved: {},
    derived: ALL,
    reference: allRef(A.processing, A.processingMobile),
  },
  {
    id: "outcome-abandoned",
    slice: "P4-E",
    screen: "Resolve fenced an unrecorded attempt: not placed, safe to retry",
    approved: {},
    derived: ALL,
    reference: allRef(A.notPlaced, A.declinedMobile),
  },
  {
    id: "checkout-unconfirmed-prompt",
    slice: "P4-E",
    screen: "Cart while an attempt is unconfirmed: checkout paused, Check its status (T6: any unconfirmed attempt pauses)",
    approved: {},
    derived: ALL,
    reference: allRef(A.unknown, A.unknownMobile),
  },

  // P4-F: My Orders
  {
    id: "orders-list",
    slice: "P4-F",
    screen: "My Orders list with confirmed, unknown, processing and declined rows",
    approved: { "desktop-1440": A.ordersList, "mobile-375": A.ordersListMobile },
    derived: ["mobile-390"],
    note: "The 375 artboard has a placeholder bar instead of a header; the header is derived.",
  },
  {
    id: "orders-list-closed-attempts",
    slice: "P4-F",
    screen: "Rows for not-placed (sold out) and failed attempts, which the server lists with an order number",
    approved: {},
    derived: ALL,
    reference: allRef(A.ordersList, A.ordersListMobile),
  },
  {
    id: "orders-list-not-placed",
    slice: "P4-F",
    screen: "A never-placed attempt (fenced by Check status): Not placed, 'Your cart is unchanged.', Try again, Dismiss",
    approved: {},
    derived: ALL,
    reference: allRef(A.ordersList, A.ordersListMobile),
    note: "Post-P4-I. The server does not list a fenced attempt; this row is a reminder kept by the browser.",
  },
  {
    id: "orders-empty",
    slice: "P4-F",
    screen: "No orders yet",
    approved: { "desktop-1440": "My Orders — empty.png" },
    derived: MOBILE,
    reference: mobileRef(A.ordersListMobile),
  },
  {
    id: "orders-loading",
    slice: "P4-F",
    screen: "My Orders while loading",
    approved: { "desktop-1440": "My Orders — loading.png" },
    derived: MOBILE,
    reference: mobileRef(A.ordersListMobile),
  },
  {
    id: "orders-error",
    slice: "P4-F",
    screen: "Orders failed to load, Try again",
    approved: { "desktop-1440": "My Orders — error.png" },
    derived: MOBILE,
    reference: mobileRef(A.ordersListMobile),
    note: "The artboard has a placeholder bar instead of a header; the header is derived.",
  },
  {
    id: "orders-limit-notice",
    slice: "P4-F",
    screen: "50 orders returned: 'Showing your 50 most recent orders'",
    approved: {},
    derived: ALL,
    reference: allRef(A.ordersList, A.ordersListMobile),
  },
  {
    id: "order-detail-confirmed",
    slice: "P4-F",
    screen: "Order detail, confirmed",
    approved: { "desktop-1440": A.detailConfirmed },
    derived: MOBILE,
    reference: mobileRef(A.detailUnknownMobile),
  },
  {
    id: "order-detail-unknown",
    slice: "P4-F",
    screen: "Order attempt detail, status unknown, Check status",
    approved: { "desktop-1440": A.detailUnknown, "mobile-375": A.detailUnknownMobile },
    derived: ["mobile-390"],
    note: "The 375 artboard has a placeholder bar instead of a header; the header is derived.",
  },
  {
    id: "order-detail-declined",
    slice: "P4-F",
    screen: "Attempt detail, payment declined, not charged",
    approved: {},
    derived: ALL,
    reference: allRef(A.detailUnknown, A.detailUnknownMobile),
  },
  {
    id: "order-detail-not-placed",
    slice: "P4-F",
    screen: "Attempt detail for a never-placed attempt: nothing charged, cart unchanged, no order created, Try again",
    approved: {},
    derived: ALL,
    reference: allRef(A.detailUnknown, A.detailUnknownMobile),
    note: "Post-P4-I. Shown from the browser's reminder; the server has no record of an order.",
  },
  {
    id: "order-detail-processing",
    slice: "P4-F",
    screen: "Order detail, still processing",
    approved: {},
    derived: ALL,
    reference: allRef(A.detailConfirmed, A.detailUnknownMobile),
  },

  // P4-G: search
  {
    id: "search-results",
    slice: "P4-G",
    screen: "Search open, collection filtered",
    approved: {},
    derived: ALL,
    reference: allRef(A.hero, A.collectionMobile),
  },
  {
    id: "search-no-results",
    slice: "P4-G",
    screen: "Search with no matching product",
    approved: {},
    derived: ALL,
    reference: allRef(A.hero, A.collectionMobile),
  },
];

// P5: the Operator Dashboard (served by the controller). Its 11 artboards are separate from the
// storefront's 28, so the storefront matrix above stays as approved. Only idle and fault active
// have phone artboards; every other state is derived at 390 and 375 (audit item 2).
export const OPERATOR_SLICES = ["P5-A", "P5-B", "P5-C", "P5-D", "P5-E"] as const;
export type OperatorSlice = (typeof OPERATOR_SLICES)[number];
export type OperatorCoverageState = Omit<CoverageState, "slice"> & { slice: OperatorSlice };

const O = {
  idle: "Operator — idle, 1440 desktop.png",
  idleMobile: "Operator — 390 mobile, idle.png",
  active: "Operator — manual fault active.png",
  activeMobile: "Operator — 390 mobile, fault active.png",
} as const;
const phones = (file: string) => ({ "mobile-390": file, "mobile-375": file });

export const OPERATOR_COVERAGE: OperatorCoverageState[] = [
  {
    id: "operator-idle",
    slice: "P5-A",
    screen: "Idle: no fault running; manual, cycle, health, activity and planned list",
    approved: { "desktop-1440": O.idle, "mobile-390": O.idleMobile },
    derived: ["mobile-375"],
    reference: { "mobile-375": O.idleMobile },
  },
  {
    id: "operator-f1-config",
    slice: "P5-B",
    screen: "F1 Database Exhaustion selected: hold_seconds and duration",
    approved: { "desktop-1440": "Operator — F1 Database Exhaustion config.png" },
    derived: MOBILE,
    reference: phones(O.idleMobile),
  },
  {
    id: "operator-f2-config",
    slice: "P5-B",
    screen: "F2 Payment Errors selected: failure_rate, status 402/503 and duration",
    approved: { "desktop-1440": "Operator — F2 Payment Errors config.png" },
    derived: MOBILE,
    reference: phones(O.idleMobile),
  },
  {
    id: "operator-f3-config",
    slice: "P5-B",
    screen: "F3 Bad Deployment selected: delay_seconds, fixed release and duration",
    approved: { "desktop-1440": "Operator — F3 Bad Deployment config.png" },
    derived: MOBILE,
    reference: phones(O.idleMobile),
  },
  {
    id: "operator-manual-active",
    slice: "P5-A",
    screen: "Manual fault active: countdown, Stop fault, controls locked",
    approved: { "desktop-1440": O.active, "mobile-390": O.activeMobile },
    derived: ["mobile-375"],
    reference: { "mobile-375": O.activeMobile },
  },
  {
    id: "operator-automatic-cycle",
    slice: "P5-C",
    screen: "Automatic cycle running: repeat and phase, phase timer, Stop cycle",
    approved: { "desktop-1440": "Operator — automatic cycle.png" },
    derived: MOBILE,
    reference: phones(O.activeMobile),
  },
  {
    id: "operator-recovering",
    slice: "P5-A",
    screen: "Recovering: waiting for services; controls unlock when health checks pass",
    approved: { "desktop-1440": "Operator — recovering.png" },
    derived: MOBILE,
    reference: phones(O.idleMobile),
  },
  {
    id: "operator-status-unknown",
    slice: "P5-A",
    screen: "Fault status unknown: stop not confirmed; Check status and Stop again",
    approved: { "desktop-1440": "Operator — fault status unknown.png" },
    derived: MOBILE,
    reference: phones(O.activeMobile),
  },
  {
    id: "operator-disconnected",
    slice: "P5-A",
    screen: "Controller unreachable: last confirmed state, Retry connection, health unknown",
    approved: { "desktop-1440": "Operator — controller disconnected.png" },
    derived: MOBILE,
    reference: phones(O.activeMobile),
  },
  // P5-C: the cycle panel in states the artboards do not show (no artboard).
  {
    id: "operator-cycle-configured",
    slice: "P5-C",
    screen: "Automatic cycle panel at the contract defaults: steppers, scenarios, shuffle, seed, planned schedule and total",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": O.idle, ...phones(O.idleMobile) },
  },
  {
    id: "operator-cycle-start-unconfirmed",
    slice: "P5-C",
    screen: "Start cycle answered 503: the start is reported as unconfirmed, status unchanged",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": O.idle, ...phones(O.idleMobile) },
  },
  {
    id: "operator-cycle-stop-unconfirmed",
    slice: "P5-C",
    screen: "Stop cycle answered 503: the fault the cycle started was not confirmed stopped; status unknown, Check status and Stop again",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": "Operator — fault status unknown.png", ...phones(O.activeMobile) },
  },
  // P5-B: what a start or stop that the controller could not confirm looks like (no artboard).
  {
    id: "operator-start-unconfirmed",
    slice: "P5-B",
    screen: "Inject fault answered 503: the start is reported as unconfirmed, status unchanged",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": O.idle, ...phones(O.idleMobile) },
  },
  {
    id: "operator-stop-unconfirmed",
    slice: "P5-B",
    screen: "Stop fault answered 503: the stop is reported as unconfirmed, status unknown, Check status and Stop again",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": "Operator — fault status unknown.png", ...phones(O.activeMobile) },
  },
  // P5-D: the artboards show health and activity only in their idle samples; these states are derived.
  {
    id: "operator-health-degraded",
    slice: "P5-D",
    screen: "Service health with a degraded, an unavailable and an unknown service, reasons, and the metrics notice",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": O.idle, ...phones(O.idleMobile) },
  },
  {
    id: "operator-disconnected-health",
    slice: "P5-D",
    screen: "Controller unreachable: every service Unknown, the last snapshot not shown as current",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": "Operator — controller disconnected.png", ...phones(O.idleMobile) },
  },
  {
    id: "operator-session-activity",
    slice: "P5-D",
    screen: "Session activity listing this session's controller events, newest first",
    approved: {},
    derived: ["desktop-1440", ...MOBILE],
    reference: { "desktop-1440": O.active, ...phones(O.activeMobile) },
  },
];

/** Every captured state, storefront then operator (the capture helper and the review page). */
export const ALL_COVERAGE: (CoverageState | OperatorCoverageState)[] = [...COVERAGE, ...OPERATOR_COVERAGE];
