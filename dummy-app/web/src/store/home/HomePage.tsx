import { useEffect } from "react";
import { useLocation } from "react-router";
import { COLLECTION_ID, scrollToCollection } from "./anchors.ts";
import { Collection } from "./Collection.tsx";
import { Hero } from "./Hero.tsx";

/**
 * Brings the collection into view and moves focus to its heading when the address names it
 * (#collection), from "Shop the collection" or the header's Products link. Keyed on the
 * location, so a second click on the same link still works.
 */
function useCollectionAnchor() {
  const location = useLocation();
  useEffect(() => {
    if (location.hash !== `#${COLLECTION_ID}`) return;
    const heading = document.getElementById(`${COLLECTION_ID}-title`);
    if (!heading) return;
    scrollToCollection();
    heading.focus({ preventScroll: true });
  }, [location.key, location.hash]);
}

export function HomePage() {
  useCollectionAnchor();
  return (
    <>
      <Hero />
      <Collection />
    </>
  );
}
