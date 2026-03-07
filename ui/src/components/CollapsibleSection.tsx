import { useState } from "react";

interface CollapsibleSectionProps {
  /** Content for the clickable header bar. */
  header: React.ReactNode;
  /** Optional content to show on the right side of the header. */
  headerRight?: React.ReactNode;
  /** Section content, shown when expanded. */
  children: React.ReactNode;
  /** Whether the section starts expanded. */
  defaultOpen?: boolean;
  /** Controlled open state (overrides internal state). */
  open?: boolean;
  /** Called when toggle is clicked. */
  onToggle?: (open: boolean) => void;
  /** Border/background color class for the container. */
  className?: string;
  /** Color class for the chevron. */
  chevronClass?: string;
  /** aria-label for accessibility. */
  label?: string;
}

export default function CollapsibleSection({
  header,
  headerRight,
  children,
  defaultOpen = false,
  open: controlledOpen,
  onToggle,
  className = "border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden",
  chevronClass = "text-gray-500",
  label,
}: CollapsibleSectionProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const isOpen = controlledOpen ?? internalOpen;

  const toggle = () => {
    const next = !isOpen;
    setInternalOpen(next);
    onToggle?.(next);
  };

  return (
    <div className={className}>
      <button
        onClick={toggle}
        aria-expanded={isOpen}
        aria-label={label ? `${isOpen ? "Collapse" : "Expand"} ${label}` : undefined}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-xs hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
      >
        <span
          className={`transition-transform text-[10px] ${chevronClass} ${isOpen ? "rotate-90" : ""}`}
          aria-hidden="true"
        >
          &#9654;
        </span>
        <span className="flex-1 flex items-center gap-2">{header}</span>
        {headerRight && <span className="shrink-0">{headerRight}</span>}
      </button>
      {isOpen && (
        <div className="border-t border-inherit">
          {children}
        </div>
      )}
    </div>
  );
}
