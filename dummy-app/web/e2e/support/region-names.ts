function escapeRegExp(title: string): string {
  return title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function sectionName(title: string): RegExp {
  return new RegExp(`^${escapeRegExp(title)}(?: .*)?$`);
}
