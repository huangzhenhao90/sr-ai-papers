/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 静态导出（部署到 Vercel/Cloudflare Pages 都可）
  output: "standalone",
};
export default nextConfig;
