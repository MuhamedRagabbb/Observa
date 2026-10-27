# Storefront browser tests and visual review

Playwright 1.64.0 (exact pin). Chromium only. Every state is captured at **1440, 390 and 375** px.

| Command (in `web/`) | What it does |
|---|---|
| `npm run e2e` | Builds both bundles and the test-only component gallery, serves them with `vite preview` on 4380 (store), 4381 (operator) and 4382 (gallery), and runs the mocked-mode tests. Screenshots go to `e2e-output/screens/<width>/<state>.png`. |
| `npm run e2e:review` | Writes `e2e-output/review/index.html`: each capture beside its approved artboard, per width. Artboards are read from `docs/design/artboards/` (local only; `ARTBOARDS_DIR` overrides). |
| `E2E_STACK_URL=http://127.0.0.1:18000/ui/next/ npx playwright test` | Stack mode: runs only `*.stack.spec.ts` against a running preview project, without mocks. Never point it at the live stack. |
| `npm run dev:store` / `npm run dev:operator` | Hot-reload dev servers on 127.0.0.1:5180 / 5181, proxying API calls to the preview project (`OBSERVA_GATEWAY_URL`, default `http://127.0.0.1:18000`; `OBSERVA_CONTROLLER_URL`, default `:18090`). Live ports 8000/8090 are refused. |

All output (`e2e-output/`) is git-ignored and kept out of the Docker build context.

## Harness rules (`support/harness.ts`)
- **Strict mocks:** only the built bundle (`/ui/next/…`, served from `dist/` on disk by the real server's rules: assets and licences as files, any other path the entry page, unknown files 404), `/favicon.ico` (404) and `/ui/config` (from fixtures) are answered without a mock. The `vite preview` servers only give each origin an address: going through them stalled for about 10 s now and then under parallel load. Every other request needs a typed handler for its own origin: `api.onGateway(method, "<Gateway OpenAPI path>", …)` answers only on the storefront origin, and `api.onController(…)` only on the operator origin. These are aborted and recorded:
  - an unmocked same-origin request;
  - any request to another origin, such as the live gateway on :8000;
  - any WebSocket.
  
  Routing covers the whole browser context, popups included. A test fails at teardown if a violation is left, unless it takes them with `api.takeViolations()`. A test with `test.fail` proves that rule.
- **No proxy behind the mocks:** `vite preview` runs with an empty proxy, so a request that slipped through could not reach any stack. Stack mode refuses a live `E2E_STACK_URL`.
- **Fixtures come from the system, not the artboards (audit item 5):** `fixtures/gateway.ts` is the seeded catalogue (checked against `scripts/init-db.sql`), typed by the generated API types.
- **Component gallery (test-only):** `gallery/` is built by `vite.gallery.config.ts` into `e2e-output/gallery-dist` and never ships (the Docker image copies only `dist/`). It shows each shared component in every state (`GALLERY_URL`) and the full and focused store headers (`?view=headers`), including items that later slices enable. Its origin serves only its own files and `/ui/config`; it has no API.
- **Captures:** `capture(stateId)` accepts only ids from `coverage.ts` or `HARNESS_STATES` (the two shells, `gallery-components`, `gallery-headers`), waits for fonts, disables animations and takes a full-page screenshot.
- **Diagnostics:** a failing test, or one with leftover violations, gets a `browser-diagnostics` attachment: console errors, failed requests, violations and the per-request routing log. Non-JSON request bodies reach handlers as text. An exception in a handler is recorded as a violation instead of crashing the run.
- **Two workers:** more parallel browsers starved the preview servers on a workstation and caused timeouts. 54 of 54 passed across 3 repeats with 2 workers.

## Coverage matrix (source of truth: `coverage.ts`)
All 28 storefront artboards are mapped exactly once (checked by `test/coverage-matrix.test.ts` against the audit inventory). **A** = approved artboard at that width, **d** = derived (no artboard). Every derived cell names a reference artboard in `coverage.ts`, for example the 375 cart sheet for all mobile cart states.

| State | Slice | 1440 | 390 | 375 | Artboard(s) |
|---|---|---|---|---|---|
| home-hero | P4-B | A | d | d | Hero background — 16 9 desktop (no mobile hero, audit item 1) |
| collection | P4-B | A | A | d | Product Collection — 1440 desktop / 390 mobile |
| collection-loading, collection-error, collection-empty | P4-B | d | d | d | none |
| cart-ready | P4-C | A | d | d | Cart drawer — ready to check out |
| cart-unavailable | P4-C | A | d | A | Cart drawer — with unavailable item / Cart sheet — 375 mobile |
| cart-empty, cart-loading, cart-error | P4-C | A | d | d | Cart drawer — empty / loading / error |
| checkout-form | P4-D | A | d | A | Checkout — 1440 desktop / 375 mobile |
| checkout-form-invalid | P4-D | d | d | d | none (reference: Checkout) |
| outcome-processing | P4-D | A | d | A | Outcome 1 / Mobile — Processing |
| outcome-success | P4-D | A | d | A | Outcome 2 / Mobile — Success |
| outcome-payment-declined | P4-D | A | d | A | Outcome 3a / Mobile — Payment failed |
| outcome-not-placed | P4-D | A | d | d | Outcome 3b — Failed, not retryable |
| outcome-payment-unavailable, outcome-failed | P4-D | d | d | d | none (copy variants of 3a and 3b) |
| outcome-unknown | P4-E | A | d | A | Outcome 4 / Mobile — Status unknown |
| outcome-still-processing, outcome-abandoned, checkout-unconfirmed-prompt | P4-E | d | d | d | none (references: Outcome 1, 3b, 4) |
| orders-list | P4-F | A | d | A | My Orders — list 1440 / 375 mobile list |
| orders-list-closed-attempts | P4-F | d | d | d | none (reference: My Orders list) |
| orders-empty, orders-loading, orders-error | P4-F | A | d | d | My Orders — empty / loading / error |
| order-detail-confirmed | P4-F | A | d | d | My Orders — order detail, confirmed |
| order-detail-unknown | P4-F | A | d | A | order detail, status unknown / 375 mobile, status unknown |
| orders-limit-notice, order-detail-declined, order-detail-processing | P4-F | d | d | d | none |
| search-results, search-no-results | P4-G | d | d | d | none |

Totals: 34 states and 102 state-width cells. 28 cells have an approved artboard (19 at 1440, 1 at 390, 8 at 375); 74 are derived. Three artboards (My Orders error, 375 list, 375 unknown detail) show a placeholder bar where the header goes, so their headers are derived from the other artboards.

## Checkout safety tests to implement (P4-D to P4-F)
These follow the approved outcome rules:
- A POST result is definite only when the response carries a `CheckoutResult` for this `attempt_ref`. Failures carry it inside the error envelope's `details` (`services/shared/web.py` `error_response`).
- **The unconfirmed-attempt record (reference, lines, time) is written before the POST is sent**, so nothing a browser does afterwards can lose an attempt that may have been placed.

**Reading responses**
- E1: 201 and 202 are read from the body. 402, 409 `insufficient_stock` and 503 or 500 are read from `details`.
- E2: Any error without `details` is unknown. That includes Gateway 502/504 dependency errors, 422 validation errors, and 409 `idempotency_conflict` or `attempt_abandoned` on POST.
- E3: A `CheckoutResult` whose `attempt_ref` differs from the attempt sent is unknown, not definite.

**Refresh and navigation**
- R1: Reload while the POST is in flight → after reload, "Status unknown" with the same `ATT-` reference. No POST is sent again.
- R2: Reload on the unknown screen → the same state. Check status reuses the reference.
- R3: Reload while processing (202) → resumes polling with `GET /api/attempts/{ref}`, never a POST.
- R4: Reload on success → the order number is re-read from the server. The cart stays cleared.
- R5: Back or forward to /checkout after success → no resubmission. An empty cart cannot be submitted.

**Multiple tabs**
- T1: Two tabs allocate references at the same moment → distinct references (the allocator is serialised across tabs).
- T2: Cart edits in one tab appear in the other (storage events).
- T3: Success in tab A empties the cart in tab B. B's checkout re-quotes and cannot submit the stale cart.
- T4: An unconfirmed attempt made in tab A appears in tab B's My Orders. Resolving it in B updates A.
- T5: Both tabs press Check status on the same reference → both read; resolve is idempotent and both end in the same state.
- T5b: A stale tab POSTs a reference already resolved as abandoned in the other tab → 409 `attempt_abandoned` without details. It follows U6, and Check status then shows not placed.
- T6: The same cart submitted while an unconfirmed attempt with the same lines exists → the user is asked to check that attempt first. A new attempt gets a new reference.

**Unresolved attempts**
- U1: Network error on POST → unknown. The record is kept until a definite outcome.
- U2: Response lost after the server committed (the mock records the order, then returns a network error) → Check status shows confirmed. No duplicate order.
- U3: Check status → GET 404 → resolve → abandoned → "not placed, safe to retry". The retry uses a new reference.
- U3b: Resolve may instead return confirmed (201), processing (202) or declined (402). That happens when the attempt was recorded between the GET and the resolve, or when resolve settles a stalled attempt. Each is shown as that outcome.
- U4: GET returns processing → polls with 1, 2, 4 and 8 s backoff for about 60 s, then "still processing, check again".
- U5: Check status itself fails (network) → stays unknown. Resolve is never called after a failed GET.
- U6: 409 `idempotency_conflict` or `attempt_abandoned` on POST (no `details`) → this POST placed nothing, but the reference stays unconfirmed. Go to Check status and keep the record, because the reference may hold another, possibly confirmed, checkout.
- U7: Every click sends exactly one POST, never retried automatically. A double-click sends one POST.
- U8: An unconfirmed record older than the 50-order server window is still checkable from My Orders.
- U9 (documented limitation): clearing site data loses unconfirmed records and the anonymous customer ID. The server still holds the attempt.

### Status after P4-D (`checkout.spec.ts`, `test/checkout-*.test.ts`)
- Covered: E1-E3 (`test/checkout-outcome.test.ts`, plus a wrong-reference e2e case), R1, R5, T2 (`cart.spec.ts`), T3, T6 (paused while unconfirmed), U1, U6, U7; two tabs pressing Place Order together send one POST, with and without Web Locks.
- How duplicates are prevented: `src/store/checkout/gate.ts` decides in one IndexedDB read-write transaction (refuse while any attempt is stored, advance the reference counter, store the attempt). IndexedDB runs such transactions one at a time across tabs; localStorage is only the mirror the screens read. Without IndexedDB the fallback is a Web Lock (or an in-tab queue) over localStorage, which reads the record back before sending.
- Left for P4-E/P4-F: R2-R4, T4, T5, T5b, U2-U5, U8 (Check status, resolve, polling, My Orders).
