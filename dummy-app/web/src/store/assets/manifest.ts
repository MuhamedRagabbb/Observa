// Product imagery, keyed by stable product ID. The files are neutral placeholder drawings
// (blocker B3: no designer photos were delivered). A designer export can replace any file
// under products/ without a code change, as long as it keeps the 4:5 portrait framing.
import hero from "./hero.svg";
import sku001 from "./products/sku-001.svg";
import sku002 from "./products/sku-002.svg";
import sku003 from "./products/sku-003.svg";
import sku004 from "./products/sku-004.svg";
import sku005 from "./products/sku-005.svg";

export const PRODUCT_IMAGES: Readonly<Record<string, string>> = {
  "sku-001": sku001,
  "sku-002": sku002,
  "sku-003": sku003,
  "sku-004": sku004,
  "sku-005": sku005,
};

/** The home hero's still life (Hero artboard), a placeholder drawing until the photo arrives. */
export const HERO_IMAGE = { src: hero, width: 720, height: 760 } as const;
