// P5-C: the automatic cycle on the Operator Dashboard (right-hand panel, Stop cycle in the banner),
// against the production build with typed controller mocks. Nothing reaches a controller: every
// POST is answered by a mock and each test counts the POSTs it caused. A cycle counts as started or
// stopped only when the mocked controller's own answer proves it; an uncertain answer is never
// retried. Configured and unconfirmed states are captured at 1440, 390 and 375 (npm run e2e:review).
import type { Locator, Page } from "@playwright/test";
import { controllerStatus, type ControllerState, type ControllerStatus } from "./fixtures/controller.ts";
import { expect, test, type MockApi, type MockReply, type MockRequest } from "./support/harness.ts";
import { banner, noSideScroll, openExpanded as open, serveStatus, stepUntil, title } from "./support/operator-page.ts";

const panel = (page: Page) => page.getByRole("region", { name: "Automatic cycle" });
const startCycle = (page: Page) => panel(page).getByRole("button", { name: "Start cycle" });
const stopCycle = (page: Page) => banner(page).getByRole("button", { name: "Stop cycle" });
const stopAgain = (page: Page) => banner(page).getByRole("button", { name: "Stop again" });
const input = (page: Page, name: string) => panel(page).getByRole("textbox", { name, exact: true });
const scenario = (page: Page, name: string) => panel(page).getByRole("checkbox", { name, exact: true });
const shuffle = (page: Page) => panel(page).getByRole("switch", { name: "Shuffle scenarios" });
const posts = (api: MockApi) => api.calls.filter((call) => call.method === "POST");
const error = (status: number, error_code: string): MockReply => ({ status, body: { error_code, message: "controller words", request_id: "ctl-1" } });

/** Live notices add their text 50 ms after they mount and the page clock is frozen: step it until the text is there. */
async function seen(page: Page, where: Locator, text: string) {
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return (await where.innerText()).includes(text);
    }, { message: `waiting for: ${text}` })
    .toBe(true);
}

type CycleBody = { seed: number; scenarios: string[]; shuffle: boolean; warmup_seconds: number; duration_seconds: number; recovery_seconds: number; repeat: number };

/** The status a controller answers once it runs exactly this cycle (fault phase, step 1). */
function runningStatus(request: CycleBody): ControllerStatus {
  const base = controllerStatus("automatic");
  const steps = request.repeat * request.scenarios.length;
  return controllerStatus("automatic", {
    auto: { ...base.auto!, repeat: 1, repeats: request.repeat, step: 1, steps, scenario: request.scenarios[0] as never, seed: request.seed, shuffle: request.shuffle, config: request as never },
  });
}

/** A controller whose state the test drives; POSTs go through the handlers. */
function controller(api: MockApi, initial: ControllerState, handlers: { start?: (request: MockRequest) => MockReply; stop?: (request: MockRequest) => MockReply; deactivate?: (request: MockRequest) => MockReply } = {}) {
  const live: { state: ControllerState; status: ControllerStatus | null } = { state: initial, status: null };
  serveStatus(api, () => ({ status: 200, body: live.status ?? controllerStatus(live.state) }));
  api.onController("POST", "/faults/auto/start", (request) => handlers.start?.(request) ?? error(500, "unexpected"));
  api.onController("POST", "/faults/auto/stop", (request) => handlers.stop?.(request) ?? error(500, "unexpected"));
  api.onController("POST", "/faults/deactivate", (request) => handlers.deactivate?.(request) ?? error(500, "unexpected"));
  api.onController("POST", "/faults/activate", () => error(500, "never from the cycle panel"));
  return live;
}

test.describe("configuration", () => {
  test("real defaults, the controller's own ranges, no sample data", async ({ page, api, capture }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await expect(panel(page).getByRole("heading", { level: 2, name: "Automatic cycle" })).toBeVisible();
    await expect(input(page, "Warmup")).toHaveValue("120");
    await expect(input(page, "Fault duration")).toHaveValue("120");
    await expect(input(page, "Recovery")).toHaveValue("90");
    await expect(input(page, "Repeats")).toHaveValue("1");
    for (const name of ["Database Exhaustion", "Payment Errors", "Bad Deployment"]) await expect(scenario(page, name)).toBeChecked();
    await expect(shuffle(page)).toHaveAttribute("aria-checked", "false");
    await expect(input(page, "Seed")).toHaveValue("42");
    await expect(panel(page)).toContainText("Total 12 min 30 s");
    await expect(panel(page).getByRole("img", { name: /^Planned schedule: 12 min 30 s in total, 3 faults?/ })).toBeVisible();
    await expect(panel(page).getByText(/sample data/i)).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    expect(posts(api)).toHaveLength(0);
    await capture("operator-cycle-configured");
  });

  test("steppers move by the contract's steps, clamp at its limits and keep focus there", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await panel(page).getByRole("button", { name: "Increase warmup" }).click();
    await expect(input(page, "Warmup")).toHaveValue("130");
    await panel(page).getByRole("button", { name: "Decrease repeats" }).click({ force: true });
    await expect(input(page, "Repeats")).toHaveValue("1");
    const minus = panel(page).getByRole("button", { name: "Decrease repeats" });
    await expect(minus).toHaveAttribute("aria-disabled", "true");
    await minus.focus();
    await page.keyboard.press("Enter");
    await expect(minus).toBeFocused();
    await input(page, "Repeats").fill("1000");
    await expect(panel(page).getByRole("button", { name: "Increase repeats" })).toHaveAttribute("aria-disabled", "true");
    await input(page, "Warmup").fill("3595");
    await panel(page).getByRole("button", { name: "Increase warmup" }).click();
    await expect(input(page, "Warmup")).toHaveValue("3600");
    await expect(panel(page)).toContainText("Too many steps to draw.");
  });

  test("the total follows the settings: warmup + repeats x scenarios x (fault + recovery)", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await input(page, "Warmup").fill("60");
    await input(page, "Fault duration").fill("120");
    await input(page, "Recovery").fill("180");
    await input(page, "Repeats").fill("3");
    await scenario(page, "Bad Deployment").uncheck();
    await expect(panel(page)).toContainText("Total 31 min"); // 60 + 3 x 2 x 300 = 1860 s
    await expect(panel(page).getByRole("img", { name: /6 faults/ })).toBeVisible();
    await input(page, "Repeats").fill("x");
    await expect(panel(page)).toContainText("Total —");
  });

  test("wrong values are explained, nothing is sent, and the first wrong field gets focus", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await input(page, "Warmup").fill("4000");
    await input(page, "Seed").fill("1.5");
    await scenario(page, "Database Exhaustion").uncheck();
    await scenario(page, "Payment Errors").uncheck();
    await scenario(page, "Bad Deployment").uncheck();
    await startCycle(page).click();
    await expect(panel(page)).toContainText("Enter 0–3600 s.");
    await expect(panel(page)).toContainText("Choose at least one scenario.");
    await expect(panel(page)).toContainText("Enter a whole number from -2147483648 to 2147483647.");
    await expect(input(page, "Warmup")).toBeFocused();
    await expect(input(page, "Warmup")).toHaveAttribute("aria-invalid", "true");
    expect(posts(api)).toHaveLength(0);
    await input(page, "Warmup").fill("60");
    await expect(panel(page)).not.toContainText("Enter 0–3600 s.");
  });
});

test.describe("starting a cycle", () => {
  test("Start cycle sends the shown settings once, and success appears only when the controller's answer shows that very cycle", async ({ page, api }) => {
    const live = controller(api, "idle", {
      start: (request) => {
        live.status = runningStatus(request.body as CycleBody);
        return { status: 200, body: live.status };
      },
    });
    await open(page, "Idle");
    await input(page, "Warmup").fill("60");
    await input(page, "Fault duration").fill("30");
    await input(page, "Recovery").fill("0");
    await input(page, "Repeats").fill("3");
    await scenario(page, "Bad Deployment").uncheck();
    await shuffle(page).click();
    await input(page, "Seed").fill("7341");
    await startCycle(page).focus();
    await page.keyboard.press("Enter");
    await seen(page, panel(page), "Controller confirmed the cycle is running.");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({
      path: "/faults/auto/start",
      body: { seed: 7341, scenarios: ["db_connection_exhaustion", "payments_errors"], shuffle: true, warmup_seconds: 60, duration_seconds: 30, recovery_seconds: 0, repeat: 3 },
    });
    await stepUntil(page, () => title(page).textContent(), "Database Exhaustion");
    await expect(stopCycle(page)).toBeVisible();
    await expect(title(page)).toBeFocused();
    await expect(panel(page)).toContainText("Cycle running. Settings are locked until it stops.");
    await expect(panel(page)).toContainText("Repeat 1 of 3 · fault phase");
  });

  test("a double click and a click while it is in flight make one request", async ({ page, api }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    const live = controller(api, "idle");
    api.onController("POST", "/faults/auto/start", async (request) => {
      await held;
      live.status = runningStatus(request.body as CycleBody);
      return { status: 200, body: live.status };
    });
    await open(page, "Idle");
    await startCycle(page).dblclick();
    await startCycle(page).click({ force: true });
    await expect(startCycle(page)).toHaveAttribute("aria-busy", "true");
    expect(posts(api)).toHaveLength(1);
    release();
    await seen(page, panel(page), "Controller confirmed the cycle is running.");
    expect(posts(api)).toHaveLength(1);
  });

  for (const [name, reply, message] of [
    ["a 503", () => error(503, "target_unavailable"), "The controller failed while handling the start. The cycle may be running: check status before trying again."],
    ["a network failure", () => "network-error" as const, "The controller didn’t answer. The cycle may or may not have started: check status before trying again."],
    ["a 200 that does not show this cycle", () => ({ status: 200, body: controllerStatus("idle") }) as MockReply, "The controller’s answer didn’t confirm the cycle. It may be running: check status before trying again."],
  ] as const) {
    test(`${name} is reported as uncertain, never as success, and never retried`, async ({ page, api, capture }, info) => {
      controller(api, "idle", { start: reply });
      await open(page, "Idle");
      await startCycle(page).click();
      await seen(page, panel(page), message);
      await expect(panel(page)).not.toContainText("Controller confirmed");
      await expect(title(page)).toHaveText("Idle");
      await page.clock.runFor(60_000);
      expect(posts(api)).toHaveLength(1);
      if (name === "a 503") {
        expect(await noSideScroll(page)).toBe(true);
        await capture("operator-cycle-start-unconfirmed");
      }
      void info;
    });
  }

  test("a refusal stays visible through the next polls until the operator changes something", async ({ page, api }) => {
    controller(api, "idle", { start: () => error(409, "controller_busy") });
    await open(page, "Idle");
    await startCycle(page).click();
    await seen(page, panel(page), "A cycle or a fault is already running. Stop it before starting a cycle.");
    await page.clock.runFor(5000);
    await stepUntil(page, async () => posts(api).length, 1);
    await expect(panel(page)).toContainText("A cycle or a fault is already running.");
    await input(page, "Seed").fill("43");
    await expect(panel(page)).not.toContainText("A cycle or a fault is already running.");
  });
});

test.describe("a running cycle", () => {
  test("shows the controller's own settings read-only, where it is, and Stop cycle", async ({ page, api, capture }, info) => {
    controller(api, "automatic");
    await open(page, "Payment Errors");
    await expect(panel(page)).toContainText("Cycle running. Settings are locked until it stops.");
    await expect(panel(page)).toContainText("Repeat 2 of 3 · fault phase");
    await expect(input(page, "Warmup")).toHaveValue("60");
    await expect(input(page, "Fault duration")).toHaveValue("120");
    await expect(input(page, "Recovery")).toHaveValue("180");
    await expect(input(page, "Repeats")).toHaveValue("3");
    await expect(input(page, "Seed")).toHaveValue("7341");
    await expect(shuffle(page)).toHaveAttribute("aria-checked", "true");
    await expect(scenario(page, "Bad Deployment")).not.toBeChecked();
    await expect(input(page, "Warmup")).toHaveJSProperty("readOnly", true);
    await expect(panel(page).locator('[data-state="now"]')).toHaveCount(1);
    await expect(panel(page).locator('[data-state="now"]')).toHaveAttribute("data-kind", "fault");
    await expect(panel(page).locator('[data-state="done"]').first()).toBeAttached();
    await expect(stopCycle(page)).toBeVisible();
    await expect(startCycle(page)).toHaveAttribute("aria-disabled", "true");
    expect(await noSideScroll(page)).toBe(true);
    // Phones capture this state collapsed in operator.spec.ts, beside the artboard.
    if (info.project.name === "desktop-1440") await capture("operator-automatic-cycle");
  });

  test("Stop cycle sends one stop and shows it only once the controller confirms", async ({ page, api }) => {
    const live = controller(api, "automatic", {
      stop: () => {
        live.state = "recovering";
        return { status: 200, body: controllerStatus("recovering") };
      },
    });
    await open(page, "Payment Errors");
    await stopCycle(page).focus();
    await page.keyboard.press("Enter");
    await seen(page, page.locator("body"), "Controller confirmed the cycle stopped.");
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/auto/stop" });
    expect(posts(api)[0]?.body).toBeUndefined();
    await expect(title(page)).toBeFocused();
  });

  test("a stop whose fault the affected service did not acknowledge: honest wording, status unknown, Stop again sends one deactivate", async ({ page, api, capture }) => {
    const live = controller(api, "automatic", {
      stop: () => {
        live.state = "unknown";
        return error(503, "target_unavailable");
      },
      deactivate: () => {
        live.state = "recovering";
        return { status: 200, body: controllerStatus("recovering") };
      },
    });
    await open(page, "Payment Errors");
    await stopCycle(page).click();
    await seen(page, page.locator("body"), "The cycle was told to stop, but the affected service didn’t acknowledge stopping its fault.");
    await stepUntil(page, () => title(page).textContent(), "Unknown");
    await seen(page, page.locator("body"), "The cycle was told to stop");
    await expect(banner(page).getByRole("button", { name: "Check status" })).toBeVisible();
    await expect(stopAgain(page)).toBeEnabled();
    expect(posts(api)).toHaveLength(1);
    await capture("operator-cycle-stop-unconfirmed");
    await page.clock.runFor(1000);
    await stopAgain(page).click();
    await expect.poll(() => posts(api).length).toBe(2);
    expect(posts(api)[1]).toMatchObject({ path: "/faults/deactivate", body: { scenario: "payments_errors" } });
    await page.clock.runFor(60_000);
    expect(posts(api)).toHaveLength(2);
  });

  test("while the cycle is stopping the controller's details are gone; Stop cycle stays available because the controller allows it", async ({ page, api }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("automatic", { auto: null }) }));
    api.onController("POST", "/faults/auto/stop", () => ({ status: 200, body: controllerStatus("recovering") }));
    await open(page, "Stopping the cycle");
    await expect(panel(page)).toContainText("Stopping the cycle");
    await expect(stopCycle(page)).not.toHaveAttribute("aria-disabled", "true");
    await stopCycle(page).click();
    await expect.poll(() => posts(api).length).toBe(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/auto/stop" });
  });

  test("a stop is sent even when the answer on screen is old (a hidden tab pauses polling); only starts wait for a fresh read", async ({ page, api }) => {
    const live = controller(api, "automatic", {
      stop: () => {
        live.state = "recovering";
        return { status: 200, body: controllerStatus("recovering") };
      },
    });
    await open(page, "Payment Errors");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.clock.runFor(10_000); // far beyond the 7.5 s a fresh answer is allowed to age
    await expect(stopCycle(page)).not.toHaveAttribute("aria-disabled", "true");
    await stopCycle(page).click();
    await expect.poll(() => posts(api).length).toBe(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/auto/stop" });
  });
});

test.describe("locks follow the controller", () => {
  const LOCKS: [ControllerState, string, string][] = [
    ["manual", "Payment Errors", "Unavailable while a manual fault is active."],
    ["recovering", "Waiting for services", "Unavailable while services recover from the last fault."],
    ["unknown", "Unknown", "Unavailable until the fault status is confirmed."],
  ];
  for (const [state, heading, notice] of LOCKS) {
    test(`${state}: the panel says why it is locked and sends nothing when pressed`, async ({ page, api }) => {
      controller(api, state);
      await open(page, heading);
      await expect(panel(page)).toContainText(notice);
      await expect(startCycle(page)).toHaveAttribute("aria-disabled", "true");
      await expect(startCycle(page)).not.toHaveAttribute("disabled", /.*/);
      await expect(input(page, "Warmup")).toHaveJSProperty("readOnly", true);
      await expect(panel(page).getByRole("button", { name: "Increase warmup" })).toHaveAttribute("aria-disabled", "true");
      await startCycle(page).focus();
      await page.keyboard.press("Enter");
      await expect(startCycle(page)).toBeFocused();
      await startCycle(page).click({ force: true });
      await scenario(page, "Bad Deployment").click({ force: true });
      await shuffle(page).click({ force: true });
      await expect(shuffle(page)).toHaveAttribute("aria-checked", "false");
      expect(posts(api)).toHaveLength(0);
    });
  }

  test("a controller that cannot be reached locks the panel", async ({ page, api }) => {
    serveStatus(api, () => "network-error");
    api.onController("POST", "/faults/auto/start", () => error(500, "unexpected"));
    await open(page, "Unreachable");
    await expect(panel(page)).toContainText("Unavailable while the controller is unreachable.");
    await startCycle(page).click({ force: true });
    expect(posts(api)).toHaveLength(0);
  });
});

test.describe("keyboard, touch and motion", () => {
  test("Tab walks the panel in reading order and every stop shows a focus ring", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await panel(page).getByRole("button", { name: "Decrease warmup" }).focus();
    const order = [
      input(page, "Warmup"),
      panel(page).getByRole("button", { name: "Increase warmup" }),
      panel(page).getByRole("button", { name: "Decrease fault duration" }),
      input(page, "Fault duration"),
      panel(page).getByRole("button", { name: "Increase fault duration" }),
    ];
    for (const target of order) {
      await page.keyboard.press("Tab");
      await expect(target).toBeFocused();
      const ring = await target.evaluate((el) => {
        const style = getComputedStyle(el);
        return (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2) || style.boxShadow !== "none" || getComputedStyle(el.parentElement ?? el).boxShadow !== "none" || getComputedStyle(el.parentElement ?? el).outlineStyle !== "none";
      });
      expect(ring, "visible focus indicator").toBe(true);
    }
  });

  test("the shuffle switch and the checkboxes work from the keyboard", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await shuffle(page).focus();
    await page.keyboard.press("Space");
    await expect(shuffle(page)).toHaveAttribute("aria-checked", "true");
    await scenario(page, "Payment Errors").focus();
    await page.keyboard.press("Space");
    await expect(scenario(page, "Payment Errors")).not.toBeChecked();
  });

  test("on phones the panel's controls are at least 44 px high and nothing scrolls sideways", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    controller(api, "idle");
    await open(page, "Idle");
    for (const control of [startCycle(page), input(page, "Warmup"), panel(page).getByRole("button", { name: "Increase warmup" }), shuffle(page), scenario(page, "Payment Errors").locator("xpath=ancestor::label")]) {
      const box = await control.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    expect(await noSideScroll(page)).toBe(true);
  });

  test("at 200% zoom (720 CSS px wide) the panel stays inside the page", async ({ page, api }, info) => {
    test.skip(info.project.name !== "desktop-1440", "the desktop page zoomed");
    controller(api, "idle");
    await page.setViewportSize({ width: 720, height: 450 });
    await open(page, "Idle");
    expect(await noSideScroll(page)).toBe(true);
    for (const control of [startCycle(page), input(page, "Seed"), input(page, "Recovery")]) {
      const box = await control.boundingBox();
      expect((box?.x ?? -1) + (box?.width ?? 0)).toBeLessThanOrEqual(720);
    }
  });

  test("reduced motion: nothing in the panel or the banner's progress animates", async ({ page, api }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    controller(api, "automatic");
    await open(page, "Payment Errors");
    const durations = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>("[data-state], [class*='progressFill'], button")].map((el) => getComputedStyle(el).transitionDuration));
    for (const duration of durations) expect(duration.split(",").every((part) => part.trim() === "0s")).toBe(true);
  });
});

test.describe("contrast of the schedule (WCAG 1.4.11, 3:1 for what carries meaning)", () => {
  test("every kind of segment has an outline at least 3:1 against the page", async ({ page, api }) => {
    controller(api, "automatic");
    await open(page, "Payment Errors");
    const ratios = await page.evaluate(() => {
      const rgb = (text: string) => (text.match(/rgba?\(([^)]+)\)/)?.[1] ?? "0,0,0").split(",").slice(0, 3).map((n) => Number(n.trim()));
      const luminance = ([r, g, b]: number[]) => {
        const lin = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
        return 0.2126 * lin(r ?? 0) + 0.7152 * lin(g ?? 0) + 0.0722 * lin(b ?? 0);
      };
      const contrast = (a: number[], b: number[]) => {
        const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
        return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
      };
      const page = rgb(getComputedStyle(document.body).backgroundColor);
      const out: Record<string, number> = {};
      for (const [name, selector] of [["fault planned", '[data-kind="fault"][data-state="planned"]'], ["quiet planned", '[data-kind="recovery"][data-state="planned"], [data-kind="warmup"][data-state="planned"]'], ["done", '[data-state="done"]'], ["now", '[data-state="now"]']] as const) {
        const el = document.querySelector<HTMLElement>(selector);
        if (!el) { out[name] = 0; continue; }
        const style = getComputedStyle(el);
        const shadow = style.boxShadow !== "none" ? rgb(style.boxShadow) : null;
        const fill = rgb(style.backgroundColor);
        out[name] = Math.max(shadow ? contrast(shadow, page) : 0, contrast(fill, page));
      }
      return out;
    });
    for (const [name, ratio] of Object.entries(ratios)) expect(ratio, `${name} segment`).toBeGreaterThanOrEqual(3);
  });
});
