import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

const iconBase = process.env.NODE_ENV === "development" ? "" : "/ui";

export const metadata: Metadata = {
  title: "HPI API",
  description: "HPI API Admin UI",
  icons: {
    icon: [
      { url: `${iconBase}/favicon-v2.ico` },
      { url: `${iconBase}/favicon-96x96.png`, sizes: "96x96", type: "image/png" },
    ],
    apple: `${iconBase}/favicon.png`,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
