const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/** API prices are integer cents; the storefront shows US dollars ("$12.00"). */
export function formatMoney(cents: number): string {
  return usd.format(cents / 100);
}
