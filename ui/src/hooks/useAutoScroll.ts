import { useEffect, useRef } from "react";

/** Scrolls a container to the bottom whenever dependencies change. */
export function useAutoScroll<T extends HTMLElement = HTMLDivElement>(
  deps: unknown[],
) {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.scrollTo(0, ref.current.scrollHeight);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}
