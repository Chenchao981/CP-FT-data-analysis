import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Ant Design/jsdom suites compete for CPU and time out under parallel workers.
// Keep the default acceptance command reproducible on developer workstations.
export default defineConfig({
  plugins: [react()],
  test: { fileParallelism: false },
});
