import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Learning Harness",
  description:
    "Chat with the learning-ai-harness local AI agent — search, memory, tools, grounded answers.",
};

// Sets the resolved theme on <html> before first paint so a stored or
// system-preferred light theme never flashes the dark fallback.
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
