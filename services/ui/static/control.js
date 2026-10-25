import {$, $$, api, escapeHTML as h, showMessage, toast} from "./common.js";

const names = {
  db_connection_exhaustion:"ضغط اتصالات قاعدة البيانات",
  payments_errors:"تعطّل محاولات الدفع",
  bad_deployment:"إصدار جديد فيه مشكلة",
};
const targets = {db_connection_exhaustion:"Orders", payments_errors:"Payments", bad_deployment:"Orders"};

export async function initControl() {
  let state = null, connected = false, busy = false, polling = false, version = 0;
  const activity = [];

  function applyState(data) {
    if (typeof data?.auto_running !== "boolean" || !(data.active === null || typeof data.active === "object")) throw new Error("Invalid controller state");
    state = data;
    connected = true;
    $("#last-updated").textContent = `آخر تحديث ${new Intl.DateTimeFormat("ar-EG", {hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(new Date())}`;
    render();
  }

  function render() {
    const panel = $("#incident-panel"), active = state?.active, automatic = state?.auto_running;
    panel.classList.toggle("active", Boolean(active) && connected);
    panel.classList.toggle("disconnected", !connected);
    $("#controller-connection").textContent = connected ? "متصل بالمتحكم" : "الاتصال بالمتحكم غير متاح";
    if (!connected) {
      $("#incident-title").textContent = "تعذّر تحديث حالة التجربة";
      $("#incident-description").textContent = "هنحاول الاتصال تلقائيًا. الحالة السابقة لا تؤكد الوضع الحالي؛ التأثيرات لها مدة صلاحية داخل الخدمات.";
    } else if (active) {
      $("#incident-title").textContent = active.status === "activation_unconfirmed" ? `التفعيل غير مؤكد — ${names[active.scenario]}` : names[active.scenario] ?? "تجربة نشطة";
      $("#incident-description").textContent = `${automatic ? "الدورة التلقائية تعمل" : "تجربة يدوية"} · الخدمة المستهدفة: ${targets[active.scenario] ?? active.root_cause_service} · يتوقف التأثير تلقائيًا عند انتهاء المدة.`;
    } else if (automatic) {
      $("#incident-title").textContent = "الدورة التلقائية شغالة";
      $("#incident-description").textContent = "مفيش عطل نشط حاليًا؛ الدورة في فترة البداية أو التعافي بين السيناريوهات.";
    } else {
      $("#incident-title").textContent = "مفيش تجربة أعطال نشطة";
      $("#incident-description").textContent = "المتحكم في وضع الأساس. اختار سيناريو، أو شغّل دورة تلقائية علشان تبدأ التجربة.";
    }
    $("#incident-details").hidden = !active;
    $("#incident-details").innerHTML = active ? `<span>رقم تجربة التقييم</span><code dir="ltr">${h(active.incident_id)}</code>` : "";
    $("#stop-incident").disabled = !connected || busy || !active;
    $("#stop-incident").textContent = busy ? "جاري التنفيذ…" : automatic ? "إيقاف الدورة والتجربة" : "إيقاف التجربة";
    $$(".scenario-card").forEach(form => {
      const current = active?.scenario === form.dataset.scenario;
      form.classList.toggle("is-active", current);
      $$("input, select", form).forEach(input => { input.disabled = !connected || busy || Boolean(active) || Boolean(automatic); });
      const button = $("button[type=submit]", form);
      button.disabled = !connected || busy || Boolean(active) || Boolean(automatic);
      button.textContent = current ? "السيناريو شغال" : "▶  تشغيل السيناريو";
    });
    $$("input", $("#auto-form")).forEach(input => { input.disabled = !connected || busy || Boolean(active) || Boolean(automatic); });
    $("#start-auto").disabled = !connected || busy || Boolean(active) || Boolean(automatic);
    $("#start-auto").textContent = automatic ? "الدورة التلقائية شغالة" : "تشغيل الدورة التلقائية ←";
    $("#stop-auto").disabled = !connected || busy || !automatic;
    updateTimer();
  }

  function updateTimer() {
    const active = state?.active;
    if (!active) {
      $("#incident-timer").textContent = state?.auto_running ? "AUTO" : "—";
      $("#incident-progress-bar").style.width = "0%";
      return;
    }
    const left = Math.max(0, Number(active.expires_at) * 1000 - Date.now());
    const total = Math.max(1, Number(active.expires_at) * 1000 - Date.parse(active.started_at));
    const seconds = Math.ceil(left / 1000);
    $("#incident-timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
    $("#incident-progress-bar").style.width = `${Math.min(100, Math.max(0, (1 - left / total) * 100))}%`;
  }

  async function poll() {
    if (polling || busy) return;
    polling = true;
    const generation = version;
    try {
      const {data} = await api("/faults/status", {timeout:5000});
      if (generation === version) applyState(data);
    } catch {
      if (generation === version) { connected = false; render(); }
    } finally { polling = false; }
  }

  function addActivity(message, failed = false) {
    activity.unshift({message, failed, time:new Date()});
    activity.splice(15);
    $("#activity-list").innerHTML = activity.map(item => `<div class="activity-row"><span><span class="status-dot ${item.failed ? "error" : "healthy"}"></span>${h(item.message)}</span><time>${h(new Intl.DateTimeFormat("ar-EG",{hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(item.time))}</time></div>`).join("");
  }

  async function mutate(path, body, successMessage) {
    if (busy || !connected) return;
    busy = true;
    version += 1;
    showMessage($("#control-message"));
    render();
    try {
      const {data} = await api(path, {method:"POST", body, timeout:15000});
      applyState(data);
      addActivity(successMessage);
      toast(successMessage);
    } catch (error) {
      const message = error.code === "validation_error" ? "راجع إعدادات التجربة وحدود القيم الموضحة قبل التشغيل." : error.message;
      showMessage($("#control-message"), message, true);
      addActivity(`تعذّر إكمال الإجراء — ${message}`, true);
    } finally {
      busy = false;
      version += 1;
      render();
      void poll();
    }
  }

  $$(".scenario-card").forEach(form => {
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      const data = new FormData(form), scenario = form.dataset.scenario;
      const parameters = scenario === "db_connection_exhaustion" ? {hold_seconds:Number(data.get("hold"))} : scenario === "payments_errors" ? {failure_rate:Number(data.get("rate")) / 100, status_code:Number(data.get("status"))} : {delay_seconds:Number(data.get("delay"))};
      void mutate("/faults/activate", {scenario, duration_seconds:Number(data.get("duration")), parameters}, `تم تشغيل: ${names[scenario]}`);
    });
  });

  $("#stop-incident").addEventListener("click", () => {
    if (!state?.active) return;
    if (state.auto_running) void mutate("/faults/auto/stop", {}, "تم إيقاف الدورة وتنظيف التجربة النشطة.");
    else void mutate("/faults/deactivate", {scenario:state.active.scenario}, "تم إيقاف التجربة وإرسال طلب استعادة التشغيل الطبيعي.");
  });
  $("#auto-form").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    if (busy || !form.reportValidity()) return;
    const data = new FormData(form), scenarios = data.getAll("scenario");
    if (!scenarios.length) { showMessage($("#control-message"), "اختار سيناريو واحد على الأقل للدورة التلقائية.", true); return; }
    void mutate("/faults/auto/start", {
      warmup_seconds:Number(data.get("warmup")), duration_seconds:Number(data.get("duration")),
      recovery_seconds:Number(data.get("recovery")), repeat:Number(data.get("repeat")),
      seed:Number(data.get("seed")), shuffle:data.has("shuffle"), scenarios,
    }, "تم بدء الدورة التلقائية بالإعدادات المحددة.");
  });
  $("#stop-auto").addEventListener("click", () => { void mutate("/faults/auto/stop", {}, "تم إيقاف الدورة التلقائية وتنظيف أي تجربة نشطة."); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void poll(); });
  await poll();
  setInterval(() => { if (!document.hidden) void poll(); }, 2500);
  setInterval(() => { if (!document.hidden) updateTimer(); }, 1000);
}
