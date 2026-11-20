# Browser tests

The Playwright suites cover the checkout storefront and the fault operator in Chromium at widths of 1440, 390 and 375 pixels. Choose a suite based on which backend you want to exercise.

## Install

Use Node.js 24 or later. From `dummy-app/web/`:

```sh
npm ci
npx playwright install chromium
```

## Run a suite

| Suite | Command from `dummy-app/web/` | What runs |
| --- | --- | --- |
| Mocked UI | `npm run e2e` | Builds the storefront, operator and component gallery. Serves them locally with typed API fixtures on ports 4380–4382. |
| Controller contract | `npm run e2e:contract` | Runs the real Python controller on port 18190 with stub targets and test controls on port 18191. Requires the Python environment described below. |
| Preview stack | `npx playwright test` with `E2E_STACK_URL` set | Runs the stack specs against an isolated Docker preview. These tests place orders and can activate faults. |

**Mocked UI.** The suite covers search, cart updates, checkout, recovery from lost responses, order history and operator controls. The fixtures reject unexpected API calls and record browser diagnostics on failure. The [coverage matrix](coverage.ts) lists the states captured at each viewport.

**Controller contract.** Create `dummy-app/.venv` and install `dummy-app/requirements-dev.txt` in that environment. The launcher uses its Python interpreter and starts its own services. The harness refuses inherited proxy settings and live service targets. It exercises controller responses and browser behaviour without a running Docker stack. Its network checks apply to the test page. Popups and dedicated workers have different coverage.

**Preview stack.** Start the [isolated Docker preview](../../README.md#isolated-docker-preview). For repeatable successful checkouts, set `BASELINE_DECLINE_RATE=0` for the preview payments service when starting it. Use a fresh terminal in `dummy-app/web/`:

```powershell
$env:E2E_STACK_URL = 'http://127.0.0.1:18000'
try { npx playwright test } finally { Remove-Item Env:E2E_STACK_URL }
```

Stack tests run serially because an injected fault affects other checkouts in the same environment. The default preview controller is on port 18090.

## Results and visual review

Open `e2e-output/report/index.html` for the Playwright report. Screenshots are saved under `e2e-output/screens/<width>/`. After the mocked suite, run `npm run e2e:review` to build `e2e-output/review/index.html`. For side-by-side design comparisons, supply the local artboards under `docs/design/artboards/` or set `ARTBOARDS_DIR`. Generated test output stays local.
