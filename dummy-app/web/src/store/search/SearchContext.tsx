// Product search state for the storefront page: whether the field is open and what was typed.
// It lives with the layout (not the collection) so the header's Search button can open it from
// any page: from another page it goes to the collection first. Nothing is stored or sent.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { useLocation, useNavigate } from "react-router";

type SearchState = {
  open: boolean;
  query: string;
  /** Counts each request to open or refocus the field, so the field can react to a repeat press. */
  focusRequests: number;
  /** The header's Search button: where focus returns when the search closes. */
  buttonRef: RefObject<HTMLButtonElement | null>;
  /** The search field, so Clear can keep the cursor in it. */
  fieldRef: RefObject<HTMLInputElement | null>;
  setQuery: (query: string) => void;
  /** The header's Search button: open the field, from the home page or by going there first. */
  request: () => void;
  /** Close the search and clear the filter; focus goes back to the Search button. */
  close: () => void;
  /** Empty the field, keeping it open with the cursor in it (also after a button that is about to disappear). */
  clear: () => void;
};

const SearchContext = createContext<SearchState | null>(null);

/** Carried by the navigation from another page, and dropped once it has been acted on. */
type OpenSearchState = { openSearch?: boolean } | null;

export function SearchProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const onHome = location.pathname === "/";
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [focusRequests, setFocusRequests] = useState(0);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const fieldRef = useRef<HTMLInputElement | null>(null);

  const openField = useCallback(() => {
    setOpen(true);
    setFocusRequests((count) => count + 1);
  }, []);

  const request = useCallback(() => {
    if (onHome) openField();
    else navigate("/", { state: { openSearch: true } satisfies OpenSearchState });
  }, [onHome, openField, navigate]);

  // Arriving from another page with a request to search: open it once, then drop the flag so
  // Back and reload do not reopen it.
  const requested = (location.state as OpenSearchState)?.openSearch === true;
  useEffect(() => {
    if (!requested) return;
    openField();
    navigate(`${location.pathname}${location.hash}`, { replace: true, state: null });
  }, [requested, openField, navigate, location.pathname, location.hash]);

  // The filter belongs to the collection: leaving the home page ends the search.
  useEffect(() => {
    if (onHome) return;
    setOpen(false);
    setQuery("");
  }, [onHome]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    buttonRef.current?.focus();
  }, []);
  const clear = useCallback(() => {
    setQuery("");
    fieldRef.current?.focus({ preventScroll: true });
  }, []);

  const value = useMemo<SearchState>(
    () => ({ open: open && onHome, query: onHome ? query : "", focusRequests, buttonRef, fieldRef, setQuery, request, close, clear }),
    [open, onHome, query, focusRequests, request, close, clear],
  );
  return <SearchContext.Provider value={value}>{children}</SearchContext.Provider>;
}

export function useSearch(): SearchState {
  const context = useContext(SearchContext);
  if (!context) throw new Error("useSearch needs a SearchProvider");
  return context;
}

/** The field's id, so the header button can say it controls it. */
export const SEARCH_ID = "product-search";
