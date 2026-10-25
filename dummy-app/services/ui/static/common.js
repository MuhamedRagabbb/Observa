export const $ = (selector, scope = document) => scope.querySelector(selector);
export const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
export const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
export const icon = name => `<svg class="icon" aria-hidden="true"><use href="/ui/assets/icons.svg#${name}"></use></svg>`;
export const money = cents => new Intl.NumberFormat("en-US", {style:"currency", currency:"USD"}).format(Number(cents) / 100);
export const newID = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

const messages = {
  payment_declined:"الدفع التجريبي اترفض. حجز المخزون اتطلب تحريره، وتقدر تجرب طلبًا جديدًا.",
  payment_unavailable:"خدمة الدفع غير متاحة حاليًا. الطلب لم يكتمل، وتمت محاولة تحرير حجز المخزون.",
  db_pool_timeout:"مفيش اتصال متاح بقاعدة البيانات حاليًا. جرّب بعد ما الضغط يقل.",
  database_unavailable:"تعذّر تنفيذ عملية في قاعدة البيانات. راجع الحالة قبل إعادة المحاولة.",
  order_persistence_error:"حصلت مشكلة أثناء حفظ الطلب. الطلب لم يكتمل، وتمت محاولة تحرير المخزون.",
  insufficient_stock:"الكمية المطلوبة مش متاحة حاليًا. حدّث المنتجات واختار كمية أقل.",
  product_not_found:"المنتج ده مش موجود في المخزون.",
  order_not_found:"ملقيناش طلب بالرقم ده. راجع الرقم وحاول مرة تانية.",
  validation_error:"راجع البيانات المدخلة. الكمية من 1 إلى 10، ومعرّف العميل حروف إنجليزية أو أرقام أو شرطة.",
  dependency_timeout:"خدمة تابعة استغرقت وقتًا أطول من المتاح. راجع نتيجة المحاولة قبل إنشاء طلب جديد.",
  dependency_unavailable:"تعذّر الوصول لخدمة من خدمات التطبيق. جرّب بعد استقرار الاتصال.",
  fault_conflict:"فيه تجربة شغالة بالفعل. أوقفها قبل تشغيل سيناريو جديد.",
  auto_running:"الدورة التلقائية شغالة. أوقفها قبل استخدام التحكم اليدوي.",
  controller_busy:"المتحكم مشغول بتجربة أو دورة حالية. أوقفها الأول.",
  target_unavailable:"الخدمة المستهدفة لم تؤكد العملية. حدّث الحالة؛ التأثير له موعد انتهاء تلقائي.",
  connection_error:"تعذّر الاتصال. تأكد إن الخدمات شغالة وحاول تحديث الصفحة.",
  network_result_unknown:"انقطع الاتصال قبل تأكيد النتيجة. النتيجة غير مؤكدة؛ راجع الطلب أو الـ trace قبل إعادة الإرسال.",
  service_unavailable:"الخدمة غير متاحة حاليًا. راجع حالة التطبيق وحاول لاحقًا.",
  internal_error:"حصل خطأ أثناء تنفيذ الطلب. تقدر تراجع تفاصيل التتبع لمعرفة اللي حصل.",
};

export class ApiError extends Error {
  constructor(code, status = 0, requestId = null, traceId = null) {
    super(messages[code] ?? (status === 409 ? "تعذّر تنفيذ العملية بسبب تعارض في الحالة. حدّث البيانات." : "تعذّر إكمال العملية. راجع حالة الخدمات وحاول مرة أخرى."));
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.traceId = traceId;
  }
}

export async function api(path, {method = "GET", body, timeout = 30000} = {}) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeout);
  const requestId = `web-${newID()}`;
  try {
    const response = await fetch(path, {
      method, signal:abort.signal, cache:"no-store", credentials:"same-origin",
      headers:{Accept:"application/json", "X-Request-ID":requestId, ...(body === undefined ? {} : {"Content-Type":"application/json"})},
      ...(body === undefined ? {} : {body:JSON.stringify(body)}),
    });
    let data = null;
    try { data = await response.json(); } catch { /* Non-JSON infrastructure failures still get a useful message. */ }
    const meta = {requestId:response.headers.get("X-Request-ID") ?? data?.request_id ?? requestId, traceId:response.headers.get("X-Trace-ID") ?? data?.trace_id ?? null};
    if (!response.ok) throw new ApiError(data?.error_code ?? "service_unavailable", response.status, meta.requestId, meta.traceId);
    if (data === null) throw new ApiError("service_unavailable", response.status, meta.requestId, meta.traceId);
    return {data, ...meta};
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(method === "GET" ? "connection_error" : "network_result_unknown", 0, requestId);
  } finally { clearTimeout(timer); }
}

export async function loadConfig() {
  try { return (await api("/ui/config", {timeout:4000})).data; }
  catch { return {mode:document.body.dataset.page, ports:{store:8000,control:8090,grafana:3000,jaeger:16686,prometheus:9090}}; }
}

export function publicURL(config, kind, path = "/") {
  const url = new URL(location.origin);
  url.port = String(config.ports[kind]);
  url.pathname = path;
  url.search = "";
  url.hash = "";
  return url.href;
}

export function bindLinks(config) {
  $$('[data-link]').forEach(link => { link.href = publicURL(config, link.dataset.link); });
}

let toastTimer;
export function toast(message, error = false) {
  const node = $("#toast");
  clearTimeout(toastTimer);
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}

export function showMessage(node, message = "", error = false) {
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = !message;
}

export function correlationHTML(record, config) {
  const requestId = record.requestId;
  const traceId = record.traceId;
  if (!requestId && !traceId) return "";
  const validTrace = /^[a-f0-9]{32}$/.test(traceId ?? "");
  return `<details class="order-details"><summary>تفاصيل المتابعة والتتبع</summary>${requestId ? `<div class="correlation-row"><span>Request ID</span><code>${escapeHTML(requestId)}</code><button type="button" class="copy-button" data-copy="${escapeHTML(requestId)}">نسخ</button></div>` : ""}${validTrace ? `<div class="correlation-row"><span>Trace ID</span><code>${escapeHTML(traceId)}</code><button type="button" class="copy-button" data-copy="${escapeHTML(traceId)}">نسخ</button><a class="trace-link" href="${escapeHTML(publicURL(config, "jaeger", `/trace/${traceId}`))}" target="_blank" rel="noopener">افتح في Jaeger ↗</a></div>` : ""}</details>`;
}

export function bindCopy() {
  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    try { await navigator.clipboard.writeText(button.dataset.copy); toast("تم نسخ المعرّف."); }
    catch { toast("النسخ غير متاح هنا. تقدر تحدد المعرّف وتنسخه يدويًا.", true); }
  });
}

export function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("ar-EG", {hour:"2-digit",minute:"2-digit", day:"numeric",month:"short"}).format(date);
}
