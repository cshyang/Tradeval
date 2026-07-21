import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:8898", viewport: { width: 1366, height: 768 } },
  webServer: {
    command: "python3 -m http.server 8898 --directory out",
    url: "http://127.0.0.1:8898",
    reuseExistingServer: false,
  },
});
