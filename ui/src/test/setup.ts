// Vitest global setup — extends expect with jest-dom matchers and
// installs a cleanup hook so each test starts with a fresh DOM.

import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
