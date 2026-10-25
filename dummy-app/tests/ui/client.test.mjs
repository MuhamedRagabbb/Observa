import assert from "node:assert/strict";
import {test} from "node:test";
import {api, ApiError, correlationHTML, escapeHTML} from "../../services/ui/static/common.js";

test("a checkout is sent once with JSON and its response correlation IDs", async t => {
  const fetch = t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/orders");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.match(options.headers["X-Request-ID"], /^web-[a-zA-Z0-9-]+$/);
    assert.deepEqual(JSON.parse(options.body), {product_id:"sku-001", quantity:2});
    return new Response(JSON.stringify({order_id:"order-test",status:"completed"}), {
      status:201, headers:{"X-Request-ID":"req-confirmed", "X-Trace-ID":"a".repeat(32)},
    });
  });
  const result = await api("/api/orders", {method:"POST", body:{product_id:"sku-001",quantity:2}});
  assert.equal(fetch.mock.callCount(), 1);
  assert.equal(result.requestId, "req-confirmed");
  assert.equal(result.traceId, "a".repeat(32));
  assert.equal(result.data.status, "completed");
});

test("payment errors retain correlation while presenting a useful translated message", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    error_code:"payment_unavailable", request_id:"req-failed", trace_id:"b".repeat(32),
    message:"Internal dependency information should not be displayed",
  }), {status:503}));
  await assert.rejects(api("/api/orders", {method:"POST", body:{}}), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 503);
    assert.equal(error.traceId, "b".repeat(32));
    assert.equal(error.requestId, "req-failed");
    assert.match(error.message, /خدمة الدفع/);
    assert.doesNotMatch(error.message, /Internal dependency/);
    return true;
  });
});

test("a lost checkout response is ambiguous and never triggers an automatic retry", async t => {
  const fetch = t.mock.method(globalThis, "fetch", async () => { throw new TypeError("network unavailable"); });
  await assert.rejects(api("/api/orders", {method:"POST", body:{}}), error => {
    assert.equal(error.code, "network_result_unknown");
    assert.match(error.message, /غير مؤكدة/);
    return true;
  });
  assert.equal(fetch.mock.callCount(), 1);
});

test("a failed read reports a connection problem without implying a purchase outcome", async t => {
  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("offline"); });
  await assert.rejects(api("/api/products"), error => error.code === "connection_error");
});

test("trace links accept only hex IDs and arbitrary displayed values are escaped", () => {
  assert.equal(escapeHTML('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  const result = correlationHTML({requestId:'"><img src=x>',traceId:"javascript:alert(1)"}, {ports:{jaeger:16686}});
  assert.doesNotMatch(result, /<img/);
  assert.doesNotMatch(result, /javascript:/);
  assert.doesNotMatch(result, /class="trace-link"/);
});
