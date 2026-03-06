import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    base: "/ui/",
    build: {
        outDir: "../src/agentic_primitives_gateway/static",
        emptyOutDir: true,
    },
    server: {
        proxy: {
            "/api": "http://localhost:8000",
            "/healthz": "http://localhost:8000",
            "/readyz": "http://localhost:8000",
        },
    },
});
