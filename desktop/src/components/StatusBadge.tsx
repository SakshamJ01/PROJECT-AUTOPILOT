export default function StatusBadge({
  label,
  tone,
}: {
  label: string;
  tone: "ok" | "bad" | "warn" | "info";
}) {
  return <span className={`status-badge ${tone}`}>{label}</span>;
}