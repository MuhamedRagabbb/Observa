export function sectionOpen(choice: { open: boolean; locked: boolean } | null, locked: boolean, defaultOpen: boolean): boolean {
  return choice?.locked === locked ? choice.open : defaultOpen;
}
