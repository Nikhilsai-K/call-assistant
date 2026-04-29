import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/widget.ts",
      formats: ["iife"],
      name: "VocalFlowWidget",
      fileName: () => "widget.js",
    },
    rollupOptions: { output: { extend: true } },
  },
});
