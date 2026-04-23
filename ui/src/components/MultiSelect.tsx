import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";

interface MultiSelectProps<T extends string> {
  /** Options to pick from. */
  options: readonly T[];
  /** Currently selected values. */
  value: readonly T[];
  onChange: (next: T[]) => void;
  /** Shown when nothing is selected. */
  placeholder: string;
  /** aria-label for the trigger button. */
  label?: string;
  className?: string;
}

/**
 * Dropdown multi-select with checkbox options.
 *
 * Closes on outside click or Escape.  The trigger shows the count of
 * selected options (or the placeholder when empty) and keeps the tiny
 * footprint of the surrounding filter-bar inputs.
 */
export default function MultiSelect<T extends string>({
  options,
  value,
  onChange,
  placeholder,
  label,
  className,
}: MultiSelectProps<T>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selected = new Set<string>(value);
  const toggle = (opt: T) => {
    const next = selected.has(opt)
      ? value.filter((v) => v !== opt)
      : [...value, opt];
    onChange(next);
  };

  const triggerLabel =
    value.length === 0
      ? placeholder
      : value.length <= 3
        ? value.join(", ")
        : `${value.length} selected`;

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label ?? placeholder}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-500 flex items-center gap-1",
          value.length > 0 && "font-medium",
        )}
      >
        <span>{triggerLabel}</span>
        <span className="text-[9px] text-gray-500" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          className="absolute z-20 mt-1 min-w-[11rem] max-h-64 overflow-y-auto rounded border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-lg text-xs"
        >
          {value.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="w-full px-2 py-1 text-left text-indigo-600 dark:text-indigo-400 hover:bg-gray-50 dark:hover:bg-gray-800 border-b border-gray-200 dark:border-gray-800"
            >
              Clear all
            </button>
          )}
          {options.map((opt) => (
            <label
              key={opt}
              className="flex items-center gap-2 px-2 py-1 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selected.has(opt)}
                onChange={() => toggle(opt)}
                className="h-3 w-3 accent-indigo-600"
              />
              <span className="text-gray-900 dark:text-gray-100">{opt}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
