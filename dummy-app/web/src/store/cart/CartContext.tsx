import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cartStore, useCartLines, type CartLine, type CartStore } from "./cart-store.ts";

type CartContextValue = {
  store: CartStore;
  lines: CartLine[];
  /** Units across all lines (the header's count). */
  units: number;
  isOpen: boolean;
  /** Opens the cart; focus returns to whatever had it when the cart closes. */
  open: () => void;
  /** Closes the cart. `restoreFocus: false` when the closing action moves focus itself. */
  close: (options?: { restoreFocus?: boolean }) => void;
};

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const store = cartStore();
  const lines = useCartLines(store);
  const [isOpen, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const restore = useRef(false);

  const open = useCallback(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpen(true);
  }, []);

  const close = useCallback(({ restoreFocus = true }: { restoreFocus?: boolean } = {}) => {
    restore.current = restoreFocus;
    setOpen(false);
  }, []);

  // Focus goes back only after the cart has unmounted: until then the page is inert, and
  // browsers refuse to focus anything inside an inert element.
  useEffect(() => {
    if (isOpen) return;
    if (restore.current) opener.current?.focus();
    restore.current = false;
    opener.current = null;
  }, [isOpen]);

  const value = useMemo(
    () => ({ store, lines, units: lines.reduce((sum, line) => sum + line.quantity, 0), isOpen, open, close }),
    [store, lines, isOpen, open, close],
  );
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const value = useContext(CartContext);
  if (!value) throw new Error("useCart needs a CartProvider");
  return value;
}
