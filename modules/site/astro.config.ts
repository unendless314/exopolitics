import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";

// Define build concurrency constants to protect from OOM peak memory issues
const DEFAULT_BUILD_CONCURRENCY = 4;
const MAX_BUILD_CONCURRENCY = 8;

const parsedConcurrency = process.env.BUILD_CONCURRENCY
  ? Number.parseInt(process.env.BUILD_CONCURRENCY, 10)
  : DEFAULT_BUILD_CONCURRENCY;

const normalizedConcurrency = Number.isFinite(parsedConcurrency)
  ? parsedConcurrency
  : DEFAULT_BUILD_CONCURRENCY;

// Enforce strict upper bound to avoid OOM in RAM-constrained environments
const buildConcurrency = Math.min(
  Math.max(1, normalizedConcurrency),
  MAX_BUILD_CONCURRENCY,
);

// https://astro.build/config
export default defineConfig({
  site: "https://your-uap-disclosure-site.com",
  i18n: {
    defaultLocale: "zh",
    locales: ["zh", "en", "ja"],
    routing: {
      prefixDefaultLocale: true,
      redirectToDefaultLocale: true,
    }
  },
  integrations: [
    tailwind({
      applyBaseStyles: false, // Prevent Tailwind from overriding global variables
    }),
  ],
  output: "static",
  build: {
    concurrency: buildConcurrency,
  },
});
