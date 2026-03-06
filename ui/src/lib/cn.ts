/** Simple class name joiner (no tailwind-merge dep to keep it lightweight). */
export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}
