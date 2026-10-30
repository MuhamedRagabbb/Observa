// @vitest-environment node
// P5-F: on Windows Playwright stops its web server with `taskkill /T /F`, which bypasses the harness's own
// signal handling, the controller's shutdown and every cleanup. A "clean shutdown" therefore needs a
// COOPERATIVE stop first: the contract run's global teardown asks the harness to stop through its
// header-protected test API and then waits (bounded) until the harness's OWN PROCESS is gone. A closed
// port is not evidence: uvicorn closes its listeners before the controller's shutdown has finished. The
// force-kill stays as the bounded fallback. Only the harness's control origin is ever contacted.
import { describe, expect, it } from "vitest";
import { cooperativeShutdown, processExists } from "../e2e/support/contract-shutdown.ts";

const control = "http://127.0.0.1:18191";
const controller = "http://127.0.0.1:18190";
const HARNESS_PID = 424242;

type Call = { url: string; init: RequestInit | undefined };

function harness(script: {
  alive?: () => Response | Promise<Response>;
  post?: () => Response | Promise<Response>;
  stillAlive?: (polls: number) => boolean;
} = {}) {
  const calls: Call[] = [];
  let polls = 0;
  let clock = 0;
  const fetchImpl = async (url: string | URL | Request, init?: RequestInit) => {
    const target = String(url);
    calls.push({ url: target, init });
    if (target === `${control}/__test__/alive`) return script.alive ? script.alive() : Response.json({ pid: HARNESS_PID });
    if (target === `${control}/__test__/shutdown`) return script.post ? script.post() : Response.json({ status: "stopping" });
    throw new Error(`unexpected request: ${target}`);
  };
  return {
    calls,
    options: {
      control,
      controller,
      fetch: fetchImpl as typeof fetch,
      sleep: async (ms: number) => { clock += ms; },
      now: () => clock,
      isAlive: (pid: number) => {
        expect(pid).toBe(HARNESS_PID);
        return script.stillAlive ? script.stillAlive(polls++) : false;
      },
      maxWaitMs: 10_000,
    },
    elapsed: () => clock,
    polls: () => polls,
  };
}

const kinds = (calls: Call[]) => calls.map((call) => `${call.init?.method ?? "GET"} ${call.url.replace(control, "")}`);

describe("cooperativeShutdown", () => {
  it("learns the harness's process id first, then asks it to stop, both with its header and no redirects", async () => {
    const h = harness();
    await cooperativeShutdown(h.options);
    expect(kinds(h.calls)).toEqual(["GET /__test__/alive", "POST /__test__/shutdown"]);
    for (const call of h.calls) {
      expect(new Headers(call.init?.headers).get("x-contract-harness")).toBe("1");
      expect(call.init?.redirect).toBe("error");
      expect(call.init?.signal).toBeDefined();
    }
  });

  it("reports exited as soon as that process is gone", async () => {
    const h = harness({ stillAlive: (n) => n < 2 });
    const result = await cooperativeShutdown(h.options);
    expect(result.outcome).toBe("exited");
    expect(h.polls()).toBe(3);
  });

  it("times out, boundedly, when the process stays", async () => {
    const h = harness({ stillAlive: () => true });
    const result = await cooperativeShutdown(h.options);
    expect(result.outcome).toBe("timeout");
    expect(h.elapsed()).toBeGreaterThanOrEqual(10_000);
    expect(h.elapsed()).toBeLessThanOrEqual(10_500);
  });

  it("does not stop anything when the harness cannot say which process it is", async () => {
    const h = harness({ alive: () => Promise.reject(new TypeError("fetch failed")) });
    expect((await cooperativeShutdown(h.options)).outcome).toBe("unreachable");
    expect(kinds(h.calls)).toEqual(["GET /__test__/alive"]);
  });

  it.each([403, 404, 500])("reports refused when the alive answer is %i, and asks for nothing", async (status) => {
    const h = harness({ alive: () => new Response("no", { status }) });
    expect((await cooperativeShutdown(h.options)).outcome).toBe("refused");
    expect(kinds(h.calls)).toEqual(["GET /__test__/alive"]);
  });

  it.each([
    ["a string", "123"],
    ["a float", 12.5],
    ["zero", 0],
    ["one", 1],
    ["a negative number", -5],
    ["null", null],
    ["huge", 2 ** 40],
    ["above the signed 32-bit range", 2 ** 31],
    ["the largest unsigned 32-bit value", 2 ** 32 - 1],
    ["this very process", process.pid],
    ["its parent", process.ppid],
  ])("refuses a process id that is %s, and asks for nothing", async (_name, pid) => {
    const h = harness({ alive: () => Response.json({ pid }) });
    const result = await cooperativeShutdown(h.options);
    expect(result.outcome).toBe("refused");
    expect(kinds(h.calls)).toEqual(["GET /__test__/alive"]);
  });

  it("reports unreachable when the stop request fails, and refused when it is refused, without polling", async () => {
    const down = harness({ post: () => Promise.reject(new TypeError("fetch failed")) });
    expect((await cooperativeShutdown(down.options)).outcome).toBe("unreachable");
    expect(down.polls()).toBe(0);
    const no = harness({ post: () => new Response("no", { status: 403 }) });
    expect((await cooperativeShutdown(no.options)).outcome).toBe("refused");
    expect(no.polls()).toBe(0);
  });

  it("never throws, whatever the failure", async () => {
    await expect(cooperativeShutdown(harness({ alive: () => { throw new Error("boom"); } }).options)).resolves.toMatchObject({ outcome: "unreachable" });
    await expect(cooperativeShutdown(harness({ post: () => { throw new Error("boom"); } }).options)).resolves.toMatchObject({ outcome: "unreachable" });
    const odd = harness({ stillAlive: () => { throw new Error("boom"); } });
    await expect(cooperativeShutdown(odd.options)).resolves.toBeDefined();
  });

  it("never reports exited when the process probe itself fails", async () => {
    const odd = harness({ stillAlive: () => { throw Object.assign(new Error("probe broke"), { code: "EINVAL" }); } });
    const result = await cooperativeShutdown(odd.options);
    expect(result.outcome).toBe("timeout");
    expect(result.detail).toContain("probe broke");
  });

  it("contacts only the harness control origin, and not even the controller", async () => {
    const h = harness({ stillAlive: (n) => n < 3 });
    await cooperativeShutdown(h.options);
    for (const call of h.calls) expect(call.url.startsWith(control), call.url).toBe(true);
  });

  it("refuses to be pointed at anything but the two harness origins", async () => {
    const h = harness();
    for (const bad of [
      { control: "http://127.0.0.1:8090", controller },
      { control, controller: "http://127.0.0.1:18090" },
      { control: "http://localhost:18191", controller },
      { control, controller: control },
    ]) {
      await expect(cooperativeShutdown({ ...h.options, ...bad })).rejects.toThrow();
    }
    expect(h.calls).toHaveLength(0);
  });
});

describe("processExists", () => {
  const failing = (code: string) => () => { throw Object.assign(new Error(code), { code }); };

  it("signals with 0, which sends nothing, and says alive when the call succeeds", () => {
    const seen: Array<[number, number | string | undefined]> = [];
    expect(processExists(123, (pid, signal) => { seen.push([pid, signal]); return true; })).toBe(true);
    expect(seen).toEqual([[123, 0]]);
  });

  it("says gone only for ESRCH", () => {
    expect(processExists(123, failing("ESRCH"))).toBe(false);
  });

  it("says alive for EPERM: the process exists but is not ours to signal", () => {
    expect(processExists(123, failing("EPERM"))).toBe(true);
  });

  it.each(["EINVAL", "EACCES", "UNKNOWN"])("propagates %s instead of calling the process gone", (code) => {
    expect(() => processExists(123, failing(code))).toThrow(code);
  });

  it("propagates an error that carries no code", () => {
    expect(() => processExists(123, () => { throw new Error("odd"); })).toThrow("odd");
  });
});
