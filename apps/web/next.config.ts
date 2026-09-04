import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Route handlers read the backend origin at runtime. Build-time rewrites
  // would retain the development API address in a production/test build.
  outputFileTracingRoot: path.join(__dirname)
};

export default nextConfig;
