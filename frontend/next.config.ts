import type { NextConfig } from "next";

// The browser calls the FHIR service through this origin, so no CORS setup is
// needed. WARDWATCH_API_URL is read when the server starts.
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
