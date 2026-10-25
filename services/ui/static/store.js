import {$, $$, api, correlationHTML, escapeHTML as h, formatTime, icon, money, newID, showMessage, toast} from "./common.js";

const productsInfo = {
  "sku-001":{name:"دفتر يوميات",category:"ورق وأفكار",description:"مساحة هادئة لكل فكرة جديدة.",bg:"#ede5d8"},
  "sku-002":{name:"مجّ القهوة",category:"رفيق كل صباح",description:"وقفة صغيرة في يوم مليان تفاصيل.",bg:"#f1e5de"},
  "sku-003":{name:"طقم أقلام",category:"أدوات الكتابة",description:"ألوان بسيطة للحاجات اللي تهمّك.",bg:"#e5ecdf"},
  "sku-004":{name:"بوستر محدود",category:"لمسة للمكتب",description:"تفصيلة مختلفة لمساحتك المفضلة.",bg:"#eee9d8"},
  "sku-005":{name:"ملصقات تنظيم",category:"ترتيب التفاصيل",description:"كل حاجة في مكانها، وبلمستك.",bg:"#e4ebeb"},
};
const statusNames = {completed:"مكتمل", failed:"لم يكتمل", unknown:"غير مؤكد", pending:"قيد التنفيذ", inventory_reserved:"المخزون محجوز", reconciliation_required:"محتاج مراجعة"};
const STORAGE_KEY = "observa.browser-orders.v1";

export async function initStore(config) {
  let products = [], selected = null, busy = false, productLoading = false;
  let orders = [];
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (Array.isArray(saved)) orders = saved.filter(item => item && typeof item.key === "string" && typeof item.status === "string").slice(0,100);
  } catch { /* An unavailable browser store must not prevent checkout. */ }
  const dialog = $("#checkout-dialog");
  const customerDefault = `web-user-${newID().slice(0,8)}`;

  function saveOrder(record) {
    const index = orders.findIndex(item => item.key === record.key);
    if (index >= 0) orders[index] = {...orders[index], ...record};
    else orders.unshift(record);
    orders = orders.slice(0,100);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(orders)); }
    catch { toast("النتيجة ظهرت، لكن حفظ سجل المتصفح غير متاح هنا. احتفظ برقم الطلب."); }
    renderOrders();
  }

  const productInfo = product => productsInfo[product.product_id] ?? {name:product.name,category:"مجموعة المكتب",description:"منتج تجريبي من مجموعة OBSERVA.",bg:"#e9edde"};
  const productName = id => productsInfo[id]?.name ?? products.find(product => product.product_id === id)?.name ?? id ?? "محاولة طلب";

  function renderProducts() {
    const query = $("#product-search").value.trim().toLowerCase();
    const availableOnly = $("#available-only").checked;
    const filtered = products.filter(product => (!availableOnly || product.stock > 0) && `${productInfo(product).name} ${product.name} ${product.product_id}`.toLowerCase().includes(query));
    $("#product-count").textContent = String(filtered.length);
    $("#product-grid").setAttribute("aria-busy", "false");
    $("#product-grid").innerHTML = filtered.length ? filtered.map(product => {
      const info = productInfo(product), available = product.stock > 0;
      const art = Object.hasOwn(productsInfo, product.product_id) ? product.product_id : "sku-001";
      return `<article class="product-card"><div class="product-visual" style="--product-bg:${info.bg}"><span class="pill">${h(info.category)}</span><svg class="product-art" viewBox="0 0 300 190" aria-hidden="true"><use href="/ui/assets/products.svg#${art}"></use></svg><span class="product-code" dir="ltr">${h(product.product_id)}</span></div><div class="product-info"><span class="product-category">مجموعة OBSERVA</span><div class="product-title-row"><h3>${h(info.name)}</h3><span class="product-price" dir="ltr">${h(money(product.price_cents))}</span></div><p class="product-description">${h(info.description)}</p><div class="product-bottom"><span class="stock-label ${available ? "" : "empty"}"><span class="status-dot"></span>${available ? "متاح في المخزون" : "نفد المخزون"}</span><button class="button ${available ? "button-primary" : "button-soft"}" data-buy="${h(product.product_id)}" ${available ? "" : "disabled"}>${available ? "اطلب المنتج" : "غير متاح"} ${icon("box")}</button></div></div></article>`;
    }).join("") : `<div class="empty-state">${icon("search")}<h3>مفيش منتجات مطابقة</h3><p>جرّب كلمة مختلفة أو غيّر فلتر التوفر.</p></div>`;
  }

  async function loadProducts(quiet = false) {
    if (productLoading) return;
    productLoading = true;
    $("#reload-products").disabled = true;
    try {
      const result = await api("/api/products");
      if (!Array.isArray(result.data)) throw new Error("Invalid product list");
      products = result.data;
      renderProducts();
      showMessage($("#catalog-message"));
    } catch (error) {
      showMessage($("#catalog-message"), error.message, true);
      if (!products.length) {
        $("#product-grid").innerHTML = `<div class="empty-state">${icon("box")}<h3>المنتجات مش متاحة حاليًا</h3><p>تأكد إن التطبيق شغال، واضغط على تحديث المنتجات.</p></div>`;
        $("#product-grid").setAttribute("aria-busy", "false");
      } else if (!quiet) toast(error.message, true);
    } finally { productLoading = false; $("#reload-products").disabled = false; }
  }

  async function checkHealth() {
    try {
      const {data} = await api("/health", {timeout:4000});
      $("#store-dot").className = `status-dot ${data.status === "ready" ? "healthy" : "error"}`;
      $("#store-health").textContent = data.status === "ready" ? "البوابة متصلة" : "الاتصال غير مستقر";
    } catch { $("#store-dot").className = "status-dot error"; $("#store-health").textContent = "البوابة غير متاحة"; }
  }

  function switchTab() {
    const ordersTab = location.hash === "#orders";
    $("#orders-view").hidden = !ordersTab;
    $("#products-view").hidden = ordersTab;
    $$('[data-tab]').forEach(link => { const active = (link.dataset.tab === "orders") === ordersTab; link.classList.toggle("active", active); if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
    if (ordersTab) renderOrders();
    if (["", "#products", "#orders"].includes(location.hash)) window.scrollTo({top:0, behavior:"instant"});
  }

  function renderOrders() {
    $("#nav-order-count").textContent = String(orders.length);
    $("#order-count").textContent = String(orders.length);
    $("#orders-list").innerHTML = orders.length ? orders.map(record => {
      const badge = record.status === "completed" ? "pill-success" : record.status === "failed" ? "pill-error" : "pill-warning";
      return `<article class="order-card"><div><h3>${h(productName(record.product_id))} <span class="pill ${badge}">${h(statusNames[record.status] ?? record.status)}</span></h3><p>${record.quantity ? `الكمية: ${h(record.quantity)} · ` : ""}${h(formatTime(record.createdAt))}</p><code class="order-id mono">${h(record.order_id ?? "محاولة بدون رقم طلب مؤكد")}</code>${record.errorMessage ? `<p>${h(record.errorMessage)}</p>` : ""}</div><div class="order-meta">${record.order_id ? `<button type="button" class="button button-outline" data-refresh-order="${h(record.order_id)}">تحديث الحالة</button><button class="copy-button" type="button" data-copy="${h(record.order_id)}">نسخ الرقم</button>` : `<span>نتيجة المحاولة محفوظة محليًا</span>`}</div>${correlationHTML(record, config)}</article>`;
    }).join("") : `<div class="empty-state">${icon("box")}<h3>أول طلب لسه مستنيك</h3><p>أنشئ طلبًا من المنتجات، أو أضف رقم طلب موجود من الخانة بالأعلى.</p><a class="button button-primary" href="#products">اختار منتجًا ←</a></div>`;
  }

  async function refreshOrder(orderId, announce = true) {
    const result = await api(`/api/orders/${encodeURIComponent(orderId)}`);
    const old = orders.find(record => record.order_id === orderId);
    // Keep the checkout trace; a later lookup has its own separate trace.
    saveOrder({...result.data, key:orderId, createdAt:old?.createdAt ?? Date.now(), requestId:old?.requestId ?? result.requestId, traceId:old?.traceId ?? result.traceId, errorMessage:result.data.error_code ? statusNames[result.data.status] : null});
    if (announce) toast("تم تحديث حالة الطلب.");
  }

  function openCheckout(productId) {
    selected = products.find(product => product.product_id === productId);
    if (!selected || selected.stock <= 0 || busy) return;
    const info = productInfo(selected);
    $("#checkout-body").innerHTML = `<span class="eyebrow muted">اختيار حلو، وخطوة واحدة فاضلة</span><h2 id="checkout-title">تفاصيل طلبك</h2><p class="checkout-subtitle">راجع الكمية واسم العميل، وخلّينا نكمل التجربة.</p><div class="checkout-product"><svg viewBox="0 0 300 190" aria-hidden="true"><use href="/ui/assets/products.svg#${Object.hasOwn(productsInfo, selected.product_id) ? selected.product_id : "sku-001"}"></use></svg><div><b>${h(info.name)}</b><small>${h(selected.product_id)} · <span dir="ltr">${h(money(selected.price_cents))}</span> للقطعة</small></div></div><form id="checkout-form"><label class="form-field">الكمية<div class="quantity-control"><button type="button" data-quantity="-1" aria-label="تقليل الكمية">−</button><input id="checkout-quantity" type="number" min="1" max="10" step="1" value="2" required aria-label="الكمية"><button type="button" data-quantity="1" aria-label="زيادة الكمية">+</button></div><small>من 1 إلى 10 قطع في الطلب الواحد</small></label><label class="form-field" for="checkout-customer">معرّف العميل<input id="checkout-customer" class="text-input" dir="ltr" value="${h(customerDefault)}" pattern="[a-zA-Z0-9_\-]+" minlength="1" maxlength="64" required autocomplete="off"><small>معرّف تجريبي بحروف إنجليزية وأرقام، من غير بيانات شخصية.</small></label><div class="checkout-total"><span>إجمالي الطلب</span><strong id="checkout-total" dir="ltr">${h(money(selected.price_cents * 2))}</strong></div><button id="checkout-submit" class="button button-primary checkout-submit" type="submit">تأكيد الطلب ${icon("check")}</button><p class="checkout-disclaimer">دفع افتراضي بالكامل · مش هيتخصم منك أي مبلغ</p></form>`;
    dialog.showModal();
    $("#checkout-quantity").focus();
    $("#checkout-form").addEventListener("submit", submitCheckout);
    $("#checkout-quantity").addEventListener("input", () => { const quantity = Number($("#checkout-quantity").value); $("#checkout-total").textContent = money(selected.price_cents * (Number.isFinite(quantity) ? quantity : 0)); });
  }

  async function submitCheckout(event) {
    event.preventDefault();
    if (busy || !event.target.reportValidity()) return;
    busy = true;
    $("[data-close-dialog]").disabled = true;
    const quantity = Number($("#checkout-quantity").value);
    const body = {product_id:selected.product_id, quantity, customer_id:$("#checkout-customer").value.trim()};
    const button = $("#checkout-submit");
    button.disabled = true;
    button.innerHTML = '<span class="loading-spinner" aria-hidden="true"></span> جاري تأكيد الطلب…';
    let record;
    try {
      const result = await api("/api/orders", {method:"POST", body});
      record = {...result.data, key:result.data.order_id, requestId:result.requestId, traceId:result.traceId, createdAt:Date.now()};
      saveOrder(record);
      showCheckoutResult(record, true);
    } catch (error) {
      const uncertain = ["network_result_unknown", "dependency_timeout", "database_unavailable"].includes(error.code);
      record = {...body, key:error.requestId ?? newID(), status:uncertain ? "unknown" : "failed", requestId:error.requestId, traceId:error.traceId, createdAt:Date.now(), errorMessage:error.message, error_code:error.code};
      saveOrder(record);
      showCheckoutResult(record, false);
    } finally { busy = false; $("[data-close-dialog]").disabled = false; void loadProducts(true); }
  }

  function showCheckoutResult(record, success) {
    const unknown = record.status === "unknown";
    $("#checkout-body").innerHTML = `<div class="checkout-result"><div class="result-icon ${success ? "" : "error"}">${icon(success ? "check" : "warning")}</div><h2 id="checkout-title">${success ? "طلبك اكتمل!" : unknown ? "النتيجة محتاجة تأكيد" : "الطلب ما اكتملش"}</h2><p>${success ? "تم حجز المنتجات وتأكيد الدفع التجريبي بنجاح. تقدر تتابع الطلب من صفحة طلباتك." : h(record.errorMessage)}</p><div class="result-summary"><b>${h(productName(record.product_id))}</b> · ${h(record.quantity)} قطعة${record.order_id ? `<code class="order-id mono">${h(record.order_id)}</code>` : `<span class="order-id">احتفظ بمعرّف المحاولة للمراجعة.</span>`}</div>${correlationHTML(record, config)}<div class="result-actions"><button type="button" class="button button-primary" data-show-orders>افتح طلباتي ←</button><button type="button" class="button button-outline" data-close-dialog>ارجع للمنتجات</button></div></div>`;
  }

  $("#product-grid").addEventListener("click", event => { const button = event.target.closest("[data-buy]"); if (button) openCheckout(button.dataset.buy); });
  $("#product-search").addEventListener("input", renderProducts);
  $("#available-only").addEventListener("change", renderProducts);
  $("#reload-products").addEventListener("click", () => { void loadProducts(); });
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.addEventListener("click", event => {
    if (busy) return;
    if (event.target.closest("[data-close-dialog]")) dialog.close();
    if (event.target.closest("[data-show-orders]")) { dialog.close(); location.hash = "orders"; }
    const quantityButton = event.target.closest("[data-quantity]");
    if (quantityButton) { const input = $("#checkout-quantity"); input.value = Math.min(10, Math.max(1, (Number(input.value) || 1) + Number(quantityButton.dataset.quantity))); input.dispatchEvent(new Event("input")); }
    if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); }
  });
  $("#lookup-form").addEventListener("submit", async event => {
    event.preventDefault();
    const id = $("#lookup-id").value.trim();
    if (!/^order-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id)) { showMessage($("#orders-message"), "اكتب رقم طلب صحيح يبدأ بـ order- ومتبوّع بالمعرّف الكامل.", true); return; }
    const button = $('button[type="submit"]', event.target);
    button.disabled = true;
    try { await refreshOrder(id); showMessage($("#orders-message")); }
    catch (error) { showMessage($("#orders-message"), error.message, true); }
    finally { button.disabled = false; }
  });
  $("#orders-list").addEventListener("click", async event => {
    const button = event.target.closest("[data-refresh-order]");
    if (!button) return;
    button.disabled = true;
    try { await refreshOrder(button.dataset.refreshOrder); }
    catch (error) { showMessage($("#orders-message"), error.message, true); }
    finally { button.disabled = false; }
  });
  $("#refresh-orders").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    let failures = 0;
    try {
      const ids = orders.filter(record => record.order_id).map(record => record.order_id);
      for (let index = 0; index < ids.length; index += 3) {
        const batch = await Promise.allSettled(ids.slice(index,index + 3).map(id => refreshOrder(id, false)));
        failures += batch.filter(result => result.status === "rejected").length;
      }
      showMessage($("#orders-message"), failures ? `تعذّر تحديث ${failures} طلب. البيانات المعروضة هي آخر حالة محفوظة.` : "تم تحديث حالات الطلبات المسجلة.", failures > 0);
    } finally { button.disabled = false; }
  });
  window.addEventListener("hashchange", switchTab);
  renderOrders(); switchTab();
  await Promise.all([loadProducts(), checkHealth()]);
  setInterval(() => { if (!document.hidden) void checkHealth(); }, 10000);
  setInterval(() => { if (!document.hidden && !busy && location.hash !== "#orders") void loadProducts(true); }, 20000);
}
