import type { NextConfig } from "next";

// The browser calls the FHIR service through this origin, so no CORS setup is
// needed. Next.js fixes rewrites at build time, so WARDWATCH_API_URL must be set for `next build`.
const apiUrl = process.env.WARDWATCH_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  // Compression buffers the Server-Sent Events stream behind the rewrite.
  compress: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
