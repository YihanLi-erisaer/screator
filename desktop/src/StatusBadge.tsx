import type { ReactNode } from "react";

export function StatusBadge({
  tone,
  children,
}: {
  tone: "success" | "error" | "neutral";
  children: ReactNode;
}) {
  const color = tone === "success" ? " imported" : tone === "error" ? " missing" : "";
  return <span className={`pill${color}`}>{children}</span>;
}
