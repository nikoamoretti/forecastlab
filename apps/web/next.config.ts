import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" },
      { source: "/demo/:path*", destination: "http://127.0.0.1:8765/demo/:path*" }
    ];
  }
};

export default nextConfig;
