import type { NextConfig } from "next";
import path from "path";

const API = process.env.FORECASTLAB_API_ORIGIN || "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/demo/:path*", destination: `${API}/demo/:path*` }
    ];
  }
};

export default nextConfig;
