/**
 * postcss.config.js — PostCSS configuration for chat-ui.
 *
 * Next.js 15 processes all CSS (global + CSS Modules) through PostCSS.
 * This file ensures the CSS loader chain is explicitly activated in
 * webpack. Without it, in some Next.js 15 configurations .css files
 * can fall through to the JavaScript loader and fail with:
 *   "Module parse failed: Unexpected token"
 *
 * Conventions (matching the project):
 *   - CommonJS (same as next.config.js, server.js)
 *   - Zero external plugins — Next.js bundles its own PostCSS pipeline.
 *     We only declare the config file so webpack's CSS loader chain
 *     is unambiguously activated.
 *
 * Reference: https://nextjs.org/docs/app/building-your-application/styling/css-modules
 */

module.exports = {
  plugins: {},
};
