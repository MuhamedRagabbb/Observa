import { useEffect, type FormEvent, type KeyboardEvent } from "react";
import { Icon } from "../../shared/ui/Icon.tsx";
import { TextButton } from "../../shared/ui/TextLink.tsx";
import { scrollToCollection } from "../home/anchors.ts";
import styles from "./SearchField.module.css";
import { SEARCH_ID, useSearch } from "./SearchContext.tsx";

/**
 * The search field above the collection. Opening it (or pressing Search again) brings the
 * collection into view and puts the cursor in the field; Escape or Close ends the search.
 */
export function SearchField() {
  const { query, focusRequests, fieldRef, setQuery, clear, close } = useSearch();

  useEffect(() => {
    scrollToCollection();
    fieldRef.current?.focus({ preventScroll: true });
  }, [focusRequests, fieldRef]);

  // On the form, so Escape works with the cursor on Clear or Close as well as in the field.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    // Escape also ends an input method's composition: leave that to the input method.
    if (event.key === "Escape" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      close();
    }
  };
  const onSubmit = (event: FormEvent) => event.preventDefault(); // results update as you type

  return (
    <form id={SEARCH_ID} role="search" aria-label="Product search" className={styles.search} onSubmit={onSubmit} onKeyDown={onKeyDown}>
      <Icon name="search" className={styles.icon} />
      <input
        ref={fieldRef}
        type="search"
        className={styles.input}
        aria-label="Search products"
        placeholder="Search products"
        value={query}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="search"
        onChange={(event) => setQuery(event.target.value)}
      />
      {query ? (
        <button
          type="button"
          className={styles.clear}
          aria-label="Clear"
          onClick={clear}
        >
          <Icon name="close" className={styles.clearIcon} />
        </button>
      ) : null}
      <TextButton onClick={close} aria-label="Close search">
        Close
      </TextButton>
    </form>
  );
}
