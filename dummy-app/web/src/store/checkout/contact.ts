// Contact validation copied from the backend (services/shared/checkout.py Contact), so the
// form never sends what the Gateway would reject. The only addition: a name of spaces only is
// refused here (the backend accepts it). Contact details are sent once and never stored.
export type Contact = { name: string; email: string };
export type ContactErrors = Partial<Record<keyof Contact, string>>;

// Pydantic counts code points; JavaScript's .length counts UTF-16 units.
const length = (value: string) => [...value].length;
const CONTROL = /[\u0000-\u001f\u007f]/;
// \p{White_Space}, not \s: the backend's \s (Unicode White_Space) also covers U+0085, which
// JavaScript's \s does not. A mismatch would let the form send what the Gateway rejects.
const EMAIL = /^[^@\p{White_Space}\u0000-\u001f]+@[^@\p{White_Space}.\u0000-\u001f]+(\.[^@\p{White_Space}.\u0000-\u001f]+)+$/u;

export function validateContact({ name, email }: Contact): ContactErrors {
  const errors: ContactErrors = {};
  if (name.trim() === "") errors.name = "Enter a name.";
  else if (CONTROL.test(name)) errors.name = "Remove line breaks and other hidden characters.";
  else if (length(name) > 80) errors.name = "Use 80 characters or fewer.";
  if (length(email) > 254 || !EMAIL.test(email)) errors.email = "Enter an email like demo@example.com.";
  return errors;
}
