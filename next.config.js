/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: {
    unoptimized: true,
  },
  env: {
    AI_GATEWAY_API_KEY: process.env.AI_GATEWAY_API_KEY,
  },
}

module.exports = nextConfig
