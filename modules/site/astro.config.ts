import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";

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
});
