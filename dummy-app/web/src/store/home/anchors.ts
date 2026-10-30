/** The collection section's id: the target of "Shop the collection" and the header's Products link. */
export const COLLECTION_ID = "collection";

/** Scrolls the collection to the top of the page, gently unless the visitor prefers less motion. */
export function scrollToCollection() {
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  document.getElementById(COLLECTION_ID)?.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" });
}
