import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
    plugins: [react()],
    resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
    test: {
        environment: "jsdom",
        globals: true,
        include: ["tests/unit/**/*.test.{ts,tsx}"],
        setupFiles: ["tests/unit/setup.ts"],
        coverage: {
            provider: "v8",
            include: ["components/**", "hooks/**", "lib/**"],
            exclude: ["lib/api/schema.ts"],
            reporter: ["text-summary", "text", "json-summary"],
            thresholds: { lines: 80 },
        },
    },
});
