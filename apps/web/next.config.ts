import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  experimental: { optimizePackageImports: ["lucide-react"] },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination:
          (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000") +
          "/v1/:path*",
      },
    ];
  },
};

export default config;
