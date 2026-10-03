import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],

  // Vite builds assets; FastAPI serves them with /api on port 8000.
});
