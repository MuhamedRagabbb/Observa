import { ButtonLink } from "../../shared/ui/Button.tsx";
import { HERO_IMAGE } from "../assets/manifest.ts";
import { COLLECTION_ID } from "./anchors.ts";
import styles from "./Hero.module.css";

/** Hero artboard (1920 desktop). No mobile hero was designed: narrow screens stack it (audit item 1). */
export function Hero() {
  return (
    <section className={styles.hero} aria-labelledby="hero-title">
      <div className={styles.copy}>
        <h1 id="hero-title" className={styles.headline}>
          Quiet objects,{" "}
          <br />
          closely <em className={styles.accent}>observed.</em>
        </h1>
        <p className={styles.lede}>
          Thoughtful stationery for everyday rituals. Every order leaves a trace behind the scenes.
        </p>
        <ButtonLink to={`/#${COLLECTION_ID}`} icon="arrow-right" size="large">
          Shop the collection
        </ButtonLink>
      </div>
      <div className={styles.art}>
        <img
          className={styles.image}
          src={HERO_IMAGE.src}
          width={HERO_IMAGE.width}
          height={HERO_IMAGE.height}
          alt=""
          decoding="async"
        />
      </div>
    </section>
  );
}
