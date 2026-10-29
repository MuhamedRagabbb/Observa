import type { KeyboardEvent } from "react";

export function blockKeyRepeat(event: KeyboardEvent<HTMLElement>) {
  if (event.repeat && (event.key === "Enter" || event.key === " ")) event.preventDefault();
}
