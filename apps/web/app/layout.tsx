import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "灰幕 · AI 跑团",
  description: "一个会记住选择、遵守规则并缓慢推进的 AI 跑团世界。",
  openGraph: {
    title: "灰港 · 黑潮王座",
    description: "过去的选择会留下痕迹，世界规则不会被一句话改写。",
    images: ["/gray-curtain-social.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
