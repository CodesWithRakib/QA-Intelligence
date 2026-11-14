import type { NextConfig } from "next";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Content-Security-Policy",
    // 'unsafe-inline'/'unsafe-eval' are required by the Next.js runtime in development.
    value:
      "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; " +
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self' ws://127.0.0.1:3000 ws://localhost:3000; " +
      "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
