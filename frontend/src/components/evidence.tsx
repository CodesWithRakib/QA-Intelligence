/* eslint-disable @next/next/no-img-element -- evidence screenshots are served by the authenticated API proxy, not static assets */
import { Pre } from "@/components/ui";
import type { Evidence } from "@/lib/types";

export function EvidenceList({ items }: { items: Evidence[] }) {
  if (items.length === 0) return <p className="text-muted">No evidence was captured for this item.</p>;
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.id}>
          <details>
            <summary className="cursor-pointer">
              <span className="font-medium">{item.label}</span>{" "}
              <span className="text-xs text-muted">
                {item.kind.replace("_", " ")} · sha256 {item.sha256.slice(0, 12)}
              </span>
            </summary>
            <div className="mt-2">
              {item.has_file ? (
                <a href={`/api/evidence/${item.id}/file`} target="_blank" rel="noreferrer">
                  <img src={`/api/evidence/${item.id}/file`} alt={`Screenshot: ${item.label}`} loading="lazy" className="max-h-96 rounded border border-line" />
                </a>
              ) : (
                <Pre>{item.content}</Pre>
              )}
            </div>
          </details>
        </li>
      ))}
    </ul>
  );
}
