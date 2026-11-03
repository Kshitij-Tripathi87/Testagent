import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Testagent — Data Quality Engineering",
  description: "DataHub-grounded contracts, automated quality checks and lineage-aware incident investigation.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
