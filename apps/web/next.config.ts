import type { NextConfig } from "next";

// The browser only ever talks to this web app; it forwards /v1 to the Control API. So the app works
// from any machine on the network and the API itself never has to be exposed. Fixed at build time.
const apiUrl = process.env.AITC_API_INTERNAL_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${apiUrl}/v1/:path*` }];
  },
};

export default config;
