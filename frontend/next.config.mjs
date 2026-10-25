/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  allowedDevOrigins: [
    '172.27.182.174',
    'localhost',
    '127.0.0.1',
  ],
}

export default nextConfig
