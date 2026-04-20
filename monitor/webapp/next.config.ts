import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  ...(process.env.STATUS_WEBAPP_TEST_DIST_DIR
    ? {
        distDir: process.env.STATUS_WEBAPP_TEST_DIST_DIR,
      }
    : {}),
};

export default nextConfig;
