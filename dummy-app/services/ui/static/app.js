import {bindCopy, bindLinks, loadConfig, showMessage} from "./common.js";

// The legacy Arabic operator page (control.html). The legacy store page was removed in P4-H:
// the English React storefront is served at / on the Gateway.
const config = await loadConfig();
bindLinks(config);
bindCopy();
try {
  const {initControl} = await import("./control.js");
  await initControl(config);
} catch {
  const node = document.querySelector("#control-message");
  if (node) showMessage(node, "تعذّر تجهيز الواجهة. حدّث الصفحة بعد التأكد إن الخدمات شغالة.", true);
}
