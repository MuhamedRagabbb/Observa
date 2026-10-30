// @vitest-environment node
// P5-F T1: the fixture's verdict must wait for every request handler still working when a test ends. A Node
// inspection that is still reading a body can find ground truth AFTER the last assertion; if the verdict does
// not join it, the violation is lost and the test passes. PendingWork is that join: work is tracked
// synchronously when the handler is entered, draining waits for it (including work that arrives meanwhile),
// and a bound turns a hung handler into a visible timeout instead of a hang or a silent pass.
import { describe, expect, it } from "vitest";
import { PendingWork } from "../e2e/support/pending-work.ts";

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

describe("PendingWork", () => {
  it("counts work from the moment it is tracked until it settles", async () => {
    const pending = new PendingWork();
    const gate = deferred();
    expect(pending.size).toBe(0);
    const tracked = pending.track(gate.promise);
    expect(pending.size).toBe(1);
    gate.resolve();
    await tracked;
    await tick();
    expect(pending.size).toBe(0);
  });

  it("hands back the work's own result, and its own failure", async () => {
    const pending = new PendingWork();
    await expect(pending.track(Promise.resolve(42))).resolves.toBe(42);
    await expect(pending.track(Promise.reject(new Error("boom")))).rejects.toThrow("boom");
    await tick();
    expect(pending.size).toBe(0);
  });

  it("resolves at once when there is nothing to wait for", async () => {
    expect(await new PendingWork().drain(1_000)).toEqual({ timedOut: false });
  });

  it("waits for deferred work, so what it recorded before settling is visible afterwards", async () => {
    // The scenario that matters: the test ends while a Node inspection is still pending; it later finds ground truth.
    const pending = new PendingWork();
    const violations: string[] = [];
    const inspection = deferred();
    pending.track((async () => {
      await inspection.promise;
      violations.push("http://127.0.0.1:18190/x: root_cause_service");
    })());
    const verdict = (async () => {
      await pending.drain(1_000);
      return violations.splice(0);
    })();
    await tick();
    expect(violations).toEqual([]); // nothing yet: the inspection has not finished
    inspection.resolve();
    expect(await verdict).toEqual(["http://127.0.0.1:18190/x: root_cause_service"]);
  });

  it("also waits for work that is tracked while it is draining", async () => {
    const pending = new PendingWork();
    const first = deferred();
    const second = deferred();
    const done: string[] = [];
    pending.track(first.promise.then(() => {
      done.push("first");
      pending.track(second.promise.then(() => done.push("second")));
    }));
    let returned = false;
    const drained = pending.drain(1_000).then((result) => {
      returned = true;
      return result;
    });
    first.resolve();
    await tick();
    expect(done).toEqual(["first"]);
    expect(returned, "drain must not return while the work tracked meanwhile is still pending").toBe(false);
    second.resolve();
    expect(await drained).toEqual({ timedOut: false });
    expect(done).toEqual(["first", "second"]);
  });

  it("does not throw because tracked work failed, and counts it as finished", async () => {
    const pending = new PendingWork();
    const failing = deferred();
    pending.track(failing.promise).catch(() => undefined);
    const drained = pending.drain(1_000);
    failing.reject(new Error("route handler failed"));
    expect(await drained).toEqual({ timedOut: false });
  });

  it("gives up after its bound and says so, so a hung handler is a visible failure rather than a hang", async () => {
    const pending = new PendingWork();
    pending.track(new Promise<void>(() => undefined));
    const started = Date.now();
    expect(await pending.drain(40)).toEqual({ timedOut: true });
    expect(Date.now() - started).toBeLessThan(2_000);
    expect(pending.size).toBe(1); // still unfinished: the caller records a violation for it
  });
});
