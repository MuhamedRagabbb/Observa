// P5-B: manual fault injection on the Operator Dashboard, against the production build with typed
// controller mocks. Nothing here reaches a controller: every POST is answered by a mock, and each
// test counts the POSTs it caused. A start or stop is shown as done only when the mocked controller
// proves it; an uncertain answer is never retried. F1/F2/F3 configuration states are captured at
// 1440, 390 and 375 beside their artboards (npm run e2e:review).
import type { Locator, Page } from "@playwright/test";
import { controllerStatus, type ControllerState } from "./fixtures/controller.ts";
import { expect, test, type MockApi, type MockReply, type MockRequest } from "./support/harness.ts";
import { banner, noSideScroll, open, serveStatus, stepUntil, title } from "./support/operator-page.ts";

const section = (page: Page) => page.getByRole("region", { name: "Manual fault injection" });
const inject = (page: Page) => section(page).getByRole("button", { name: "Inject fault" });
const stopFault = (page: Page) => banner(page).getByRole("button", { name: "Stop fault" });
const stopAgain = (page: Page) => banner(page).getByRole("button", { name: "Stop again" });
const scenario = (page: Page, name: string) => section(page).getByRole("radio", { name, exact: true });
const field = (page: Page, name: string) => section(page).getByRole("textbox", { name, exact: true });
const posts = (api: MockApi) => api.calls.filter((call) => call.method === "POST");

/** Live notices add their text 50 ms after they mount and the page clock is frozen: step it until the text is there. */
async function seen(page: Page, where: Locator, text: string) {
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return (await where.innerText()).includes(text);
    }, { message: `waiting for: ${text}` })
    .toBe(true);
}
const choose = (page: Page, text: RegExp) => section(page).locator("label").filter({ hasText: text }).click();

const error = (status: number, error_code: string): MockReply => ({ status, body: { error_code, message: "controller words", request_id: "ctl-1" } });

/** A controller whose state the test drives; POSTs go through `onActivate` / `onDeactivate`. */
function controller(api: MockApi, initial: ControllerState, handlers: { activate?: (request: MockRequest) => MockReply; deactivate?: (request: MockRequest) => MockReply } = {}) {
  const live = { state: initial };
  serveStatus(api, () => ({ status: 200, body: controllerStatus(live.state) }));
  api.onController("POST", "/faults/activate", (request) => handlers.activate?.(request) ?? error(500, "unexpected"));
  api.onController("POST", "/faults/deactivate", (request) => handlers.deactivate?.(request) ?? error(500, "unexpected"));
  return live;
}

test.describe("scenario configuration", () => {
  test("F2 Payment Errors is the default: rate, status code and duration, real defaults", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await expect(section(page).getByRole("heading", { level: 2, name: "Manual fault injection" })).toBeVisible();
    await expect(scenario(page, "Payment Errors")).toBeChecked();
    await expect(field(page, "Failure rate")).toHaveValue("90");
    await expect(section(page).getByRole("radio", { name: /503 Service Unavailable/ })).toBeChecked();
    await expect(field(page, "Duration")).toHaveValue("120");
    await expect(section(page)).toContainText("= 2 min");
    await expect(section(page)).toContainText("0–100% · default 90%. Share of payment requests that fail.");
    await expect(section(page)).toContainText("0.1–1800 s. Stops automatically when time runs out.");
    await expect(section(page).getByText(/sample data/i)).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    expect(posts(api)).toHaveLength(0);
  });

  test("F1 Database Exhaustion: connection hold only", async ({ page, api, capture }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await scenario(page, "Database Exhaustion").check();
    await expect(section(page)).toContainText("DATABASE EXHAUSTION · PARAMETERS");
    await expect(field(page, "Connection hold")).toHaveValue("8");
    await expect(section(page)).toContainText("0.1–15 s · default 8 s. How long each orders database connection is held.");
    await expect(section(page).getByRole("radiogroup", { name: "HTTP status" })).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-f1-config");
  });

  test("F2 Payment Errors: the 402 / 503 choice changes what would be sent", async ({ page, api, capture }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await choose(page, /402 Payment Required/);
    await expect(section(page).getByRole("radio", { name: /402 Payment Required/ })).toBeChecked();
    await expect(section(page).getByRole("radio", { name: /503 Service Unavailable/ })).not.toBeChecked();
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-f2-config");
  });

  test("F3 Bad Deployment: added delay, and a release that is shown but cannot be changed", async ({ page, api, capture }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await scenario(page, "Bad Deployment").check();
    await expect(section(page)).toContainText("BAD DEPLOYMENT · PARAMETERS");
    await expect(field(page, "Added delay")).toHaveValue("1.5");
    await expect(section(page)).toContainText("1–2 s · default 1.5 s.");
    await expect(section(page)).toContainText("Release");
    await expect(section(page)).toContainText("v2-buggy");
    await expect(section(page).getByRole("textbox", { name: /release/i })).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-f3-config");
  });

  test("the slider and the number are two views of one value", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    const slider = section(page).getByRole("slider", { name: "Failure rate slider" });
    await field(page, "Failure rate").fill("40");
    await expect(slider).toHaveValue("40");
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await expect(field(page, "Failure rate")).toHaveValue("41");
    await field(page, "Failure rate").fill("4x");
    await expect(slider).toHaveValue("41");
  });
});

test.describe("starting a fault", () => {
  test("Inject fault sends the shown settings once, and success appears only when the controller confirms", async ({ page, api }) => {
    const live = controller(api, "idle", {
      activate: () => {
        live.state = "manual";
        return { status: 200, body: controllerStatus("manual") };
      },
    });
    await open(page, "Idle");
    await field(page, "Duration").fill("300");
    await inject(page).focus();
    await page.keyboard.press("Enter");
    await seen(page, section(page), "Controller confirmed the fault is active.");
    await stepUntil(page, () => title(page).textContent(), "Payment Errors");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/activate", body: { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } } });
    await expect(stopFault(page)).toBeVisible();
    await expect(title(page)).toBeFocused();
    await expect(section(page)).toContainText("One manual fault at a time. Stop the active fault to change parameters.");
  });

  test("F1 and F3 send their own settings, never a release", async ({ page, api }) => {
    controller(api, "idle", { activate: () => error(409, "controller_busy") });
    await open(page, "Idle");
    await scenario(page, "Database Exhaustion").check();
    await field(page, "Connection hold").fill("3.5");
    await inject(page).click();
    await seen(page, section(page), "The controller is busy. Try again in a moment.");
    await page.clock.runFor(1000);
    await scenario(page, "Bad Deployment").check();
    await field(page, "Added delay").fill("2");
    await field(page, "Duration").fill("45");
    await inject(page).click();
    await expect.poll(() => posts(api).length).toBe(2);
    expect(posts(api).map((call) => call.body)).toEqual([
      { scenario: "db_connection_exhaustion", duration_seconds: 120, parameters: { hold_seconds: 3.5 } },
      { scenario: "bad_deployment", duration_seconds: 45, parameters: { delay_seconds: 2 } },
    ]);
  });

  test("a double click and a click while it is in flight make one request", async ({ page, api }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    const live = controller(api, "idle", {
      activate: () => {
        live.state = "manual";
        return { status: 200, body: controllerStatus("manual") };
      },
    });
    api.onController("POST", "/faults/activate", async () => {
      await held;
      live.state = "manual";
      return { status: 200, body: controllerStatus("manual") };
    });
    await open(page, "Idle");
    await inject(page).dblclick();
    await inject(page).click({ force: true });
    await expect(inject(page)).toHaveAttribute("aria-busy", "true");
    expect(posts(api)).toHaveLength(1);
    release();
    await seen(page, section(page), "Controller confirmed the fault is active.");
    expect(posts(api)).toHaveLength(1);
  });

  test("bad numbers are explained and nothing is sent; the first wrong field gets focus", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await field(page, "Failure rate").fill("101");
    await field(page, "Duration").fill("0");
    await inject(page).click();
    await expect(section(page)).toContainText("Enter 0–100 %.");
    await expect(section(page)).toContainText("Enter 0.1–1800 s.");
    await expect(field(page, "Failure rate")).toBeFocused();
    await expect(field(page, "Failure rate")).toHaveAttribute("aria-invalid", "true");
    expect(posts(api)).toHaveLength(0);
    await field(page, "Failure rate").fill("50");
    await expect(section(page)).not.toContainText("Enter 0–100 %.");
  });

  test("an answer that cannot be confirmed is reported as uncertain, is not shown as success, and is never retried", async ({ page, api, capture }) => {
    controller(api, "idle", { activate: () => error(503, "target_unavailable") });
    await open(page, "Idle");
    await inject(page).click();
    await seen(page, section(page), "The affected service didn’t acknowledge the start. The fault may be running: check status before trying again.");
    await expect(section(page)).not.toContainText("Controller confirmed");
    await expect(title(page)).toHaveText("Idle");
    await page.clock.runFor(60_000);
    await stepUntil(page, async () => posts(api).length, 1);
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-start-unconfirmed");
  });

  test("a refusal stays visible through the next polls until the operator changes something", async ({ page, api }) => {
    controller(api, "idle", { activate: () => error(422, "validation_error") });
    await open(page, "Idle");
    await inject(page).click();
    await seen(page, section(page), "The controller rejected these settings. Check the values and try again.");
    await page.clock.runFor(5000);
    await stepUntil(page, async () => posts(api).length, 1);
    await expect(section(page)).toContainText("The controller rejected these settings.");
    await field(page, "Duration").fill("100");
    await expect(section(page)).not.toContainText("The controller rejected these settings.");
  });

  test("a network failure is uncertain too, with no retry", async ({ page, api }) => {
    controller(api, "idle", { activate: () => "network-error" });
    await open(page, "Idle");
    await inject(page).click();
    await seen(page, section(page), "The controller didn’t answer. The fault may or may not have started: check status before trying again.");
    await page.clock.runFor(30_000);
    expect(posts(api)).toHaveLength(1);
  });
});

test.describe("stopping a fault", () => {
  test("Stop fault sends one stop for the running fault and shows it only once the controller confirms", async ({ page, api }) => {
    const live = controller(api, "manual", {
      deactivate: () => {
        live.state = "recovering";
        return { status: 200, body: controllerStatus("recovering") };
      },
    });
    await open(page, "Payment Errors");
    await stopFault(page).focus();
    await page.keyboard.press("Enter");
    await seen(page, page.locator("body"), "Controller confirmed the fault stopped.");
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/deactivate", body: { scenario: "payments_errors" } });
    await expect(title(page)).toBeFocused();
  });

  test("a stop the affected service did not acknowledge: honest wording, status unknown, Stop again sends one more stop", async ({ page, api, capture }) => {
    // The first stop is not acknowledged (the controller then reports "unknown"); the second works.
    let calls = 0;
    const live = controller(api, "manual", {
      deactivate: () => {
        calls += 1;
        if (calls === 1) {
          live.state = "unknown";
          return error(503, "target_unavailable");
        }
        live.state = "recovering";
        return { status: 200, body: controllerStatus("recovering") };
      },
    });
    await open(page, "Payment Errors");
    await stopFault(page).click();
    await seen(page, page.locator("body"), "The affected service didn’t acknowledge the stop. The fault may still be running: check status, then stop again.");
    await stepUntil(page, () => title(page).textContent(), "Unknown");
    await seen(page, page.locator("body"), "The affected service didn’t acknowledge the stop.");
    await expect(banner(page).getByRole("button", { name: "Check status" })).toBeVisible();
    await expect(stopAgain(page)).toBeEnabled();
    expect(posts(api)).toHaveLength(1);
    await capture("operator-stop-unconfirmed");
    await page.clock.runFor(1000); // a deliberate second press, not the tail of the first click
    await stopAgain(page).click();
    await expect.poll(() => posts(api).length).toBe(2);
    await page.clock.runFor(60_000);
    expect(posts(api)).toHaveLength(2);
  });

  test("when the controller does not allow a stop, Stop again is unavailable and sends nothing", async ({ page, api }) => {
    api.onController("POST", "/faults/deactivate", () => error(500, "unexpected"));
    serveStatus(api, () => ({
      status: 200,
      body: controllerStatus("unknown", { controls: { ...controllerStatus("unknown").controls, deactivate: { enabled: false, reason: "auto_running" } } }),
    }));
    await open(page, "Unknown");
    await stopAgain(page).focus();
    await expect(stopAgain(page)).toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Enter");
    await stopAgain(page).click({ force: true });
    expect(posts(api)).toHaveLength(0);
    await expect(stopAgain(page)).toBeFocused();
  });

  test("the idle banner's Stop is only a picture of the artboard: unavailable, focusable, sends nothing", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    const stop = banner(page).getByRole("button", { name: "Stop", exact: true });
    await expect(stop).toHaveAttribute("aria-disabled", "true");
    await stop.click({ force: true });
    expect(posts(api)).toHaveLength(0);
  });
});

test.describe("locks follow the controller", () => {
  const LOCKS: [ControllerState, string, string][] = [
    ["manual", "Payment Errors", "One manual fault at a time. Stop the active fault to change parameters."],
    ["automatic", "Payment Errors", "Unavailable while an automatic cycle is running."],
    ["recovering", "Waiting for services", "Unavailable while services recover from the last fault."],
    ["unknown", "Unknown", "Unavailable until the fault status is confirmed."],
  ];
  for (const [state, heading, notice] of LOCKS) {
    test(`${state}: the section says why it is locked and sends nothing when pressed`, async ({ page, api }) => {
      controller(api, state);
      await open(page, heading);
      await expect(section(page)).toContainText(notice);
      await expect(inject(page)).toHaveAttribute("aria-disabled", "true");
      await expect(inject(page)).not.toHaveAttribute("disabled", /.*/);
      await expect(field(page, "Duration")).toHaveAttribute("aria-disabled", "true");
      await expect(field(page, "Duration")).toHaveJSProperty("readOnly", true);
      await inject(page).focus();
      await page.keyboard.press("Enter");
      await expect(inject(page)).toBeFocused();
      await inject(page).click({ force: true });
      await scenario(page, "Bad Deployment").click({ force: true });
      await expect(scenario(page, "Bad Deployment")).not.toBeChecked();
      expect(posts(api)).toHaveLength(0);
    });
  }

  test("manual: the running fault's own settings and an Active tag are shown", async ({ page, api, capture }) => {
    controller(api, "manual");
    await open(page, "Payment Errors");
    await expect(section(page).getByText("Active", { exact: true })).toBeVisible();
    await expect(field(page, "Failure rate")).toHaveValue("90");
    await expect(field(page, "Duration")).toHaveValue("300");
    await expect(section(page)).toContainText("= 5 min");
    await expect(stopFault(page)).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-manual-active");
  });

  test("a controller that cannot be reached locks the section", async ({ page, api }) => {
    serveStatus(api, () => "network-error");
    api.onController("POST", "/faults/activate", () => error(500, "unexpected"));
    await open(page, "Unreachable");
    await expect(section(page)).toContainText("Unavailable while the controller is unreachable.");
    await inject(page).click({ force: true });
    expect(posts(api)).toHaveLength(0);
  });
});

test.describe("keyboard and touch", () => {
  test("Tab walks the form in reading order and every stop shows a focus ring", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await scenario(page, "Payment Errors").focus();
    const order = [
      section(page).getByRole("slider", { name: "Failure rate slider" }),
      field(page, "Failure rate"),
      section(page).getByRole("radio", { name: /503 Service Unavailable/ }),
      field(page, "Duration"),
      inject(page),
    ];
    for (const target of order) {
      await page.keyboard.press("Tab");
      await expect(target).toBeFocused();
      const ring = await target.evaluate((el) => {
        const style = getComputedStyle(el);
        return (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2) || getComputedStyle(el.closest("label") ?? el).outlineStyle !== "none" || style.boxShadow !== "none";
      });
      expect(ring, "visible focus indicator").toBe(true);
    }
  });

  test("arrow keys move between scenarios", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await scenario(page, "Payment Errors").focus();
    await page.keyboard.press("ArrowDown");
    await expect(scenario(page, "Bad Deployment")).toBeChecked();
    await page.keyboard.press("ArrowUp");
    await expect(scenario(page, "Payment Errors")).toBeChecked();
  });

  test("on phones the controls are at least 44 px high", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    controller(api, "idle");
    await open(page, "Idle");
    for (const control of [inject(page), field(page, "Failure rate"), field(page, "Duration"), scenario(page, "Database Exhaustion").locator("xpath=ancestor::label")]) {
      const box = await control.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });

  test("on phones Stop fault is at least 44 px high and the page does not scroll sideways", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    controller(api, "manual");
    await open(page, "Payment Errors");
    const box = await stopFault(page).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    expect(await noSideScroll(page)).toBe(true);
  });

  test("at 200% zoom (720 CSS px wide) the form stays inside the page", async ({ page, api }, info) => {
    test.skip(info.project.name !== "desktop-1440", "the desktop page zoomed");
    controller(api, "idle");
    await page.setViewportSize({ width: 720, height: 450 });
    await open(page, "Idle");
    expect(await noSideScroll(page)).toBe(true);
    for (const control of [inject(page), field(page, "Duration"), field(page, "Failure rate")]) {
      const box = await control.boundingBox();
      expect((box?.x ?? -1) + (box?.width ?? 0)).toBeLessThanOrEqual(720);
    }
  });
});
