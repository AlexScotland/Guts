import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Loop Designer",
  description:
    "Design agent-loop orchestration as a JSON graph over the learning-ai-harness primitives — connect, branch, retry, run.",
};

// Sets the resolved theme on <html> before first paint (same storage key as
// chat-ui, so the two frontends share one light/dark preference).
const themeScript = `
(function () {
  try {
    var stored = localStorage.getItem("learning-ai-harness.theme");
    var theme =
      stored === "light" || stored === "dark"
        ? stored
        : window.matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark";
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = "dark";
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
