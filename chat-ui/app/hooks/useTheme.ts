"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const THEME_KEY = "learning-ai-harness.theme";

function detectTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

/** Light/dark theme, persisted and applied on <html data-theme>. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>("dark");

  // Hydrate client-side (the pre-paint value is set by an inline script
  // in the layout, so there is no flash and the hook never mis-renders).
  useEffect(() => {
    const t = detectTheme();
    setTheme(t);
    document.documentElement.dataset.theme = t;
  }, []);

  const toggle = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try {
        window.localStorage.setItem(THEME_KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return { theme, toggle };
}
