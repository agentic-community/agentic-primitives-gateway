import { useCallback, useState } from "react";

interface SharedWithInputProps {
  value: string[];
  onChange: (v: string[]) => void;
  ownerId?: string;
}

export default function SharedWithInput({ value, onChange, ownerId }: SharedWithInputProps) {
  const [input, setInput] = useState("");

  const addGroup = useCallback(() => {
    const trimmed = input.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
    }
    setInput("");
  }, [input, value, onChange]);

  const removeGroup = useCallback(
    (group: string) => {
      onChange(value.filter((g) => g !== group));
    },
    [value, onChange],
  );

  const toggleWildcard = useCallback(() => {
    if (value.includes("*")) {
      onChange(value.filter((g) => g !== "*"));
    } else {
      onChange(["*", ...value.filter((g) => g !== "*")]);
    }
  }, [value, onChange]);

  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
        Shared With
        {ownerId && (
          <span className="ml-2 font-normal text-gray-400 dark:text-gray-500">
            owner: {ownerId}
          </span>
        )}
      </label>
      <div className="rounded border border-gray-300 dark:border-gray-700 p-2 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {value.length === 0 && (
            <span className="text-[11px] text-gray-400 dark:text-gray-500 italic">
              Private (only owner can access)
            </span>
          )}
          {value.map((group) => (
            <span
              key={group}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono ${
                group === "*"
                  ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
                  : "bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400"
              }`}
            >
              {group === "*" ? "all users" : group}
              <button
                type="button"
                onClick={() => removeGroup(group)}
                className="hover:text-red-500 dark:hover:text-red-400"
              >
                x
              </button>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Add group name..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addGroup();
              }
            }}
            className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs font-mono"
          />
          <button
            type="button"
            onClick={addGroup}
            disabled={!input.trim()}
            className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-[11px] text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
          >
            Add
          </button>
          <button
            type="button"
            onClick={toggleWildcard}
            className={`rounded px-2 py-1 text-[11px] font-medium ${
              value.includes("*")
                ? "bg-green-600 text-white hover:bg-green-700"
                : "border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
          >
            {value.includes("*") ? "Public" : "Make Public"}
          </button>
        </div>
      </div>
    </div>
  );
}
