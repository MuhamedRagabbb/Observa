import {bindCopy, bindLinks, loadConfig, showMessage} from "./common.js";

const config = await loadConfig();
bindLinks(config);
bindCopy();
try {
  if (document.body.dataset.page === "store") {
    const {initStore} = await import("./store.js");
    await initStore(config);
  } else {
    const {initControl} = await import("./control.js");
    await initControl(config);
  }
} catch {
  const node = document.querySelector("#catalog-message, #control-message");
  if (node) showMessage(node, "تعذّر تجهيز الواجهة. حدّث الصفحة بعد التأكد إن الخدمات شغالة.", true);
}
