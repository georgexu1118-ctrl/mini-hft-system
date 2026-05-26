import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mini HFT Dashboard",
  description: "Real-time trading simulator — CLOB matching engine, WebSocket feed, latency profiling",
  icons: { icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>📈</text></svg>" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="font-mono antialiased">{children}</body>
    </html>
  );
}
