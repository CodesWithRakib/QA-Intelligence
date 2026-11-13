"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { QueryView } from "@/components/query";
import { Badge, Empty, Input, Pager, Select, SeverityBadge, StateBadge, Table } from "@/components/ui";
import { api, formatDate, query } from "@/lib/api";
import type { Category, Finding, Page } from "@/lib/types";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const STATUSES = ["open", "fix_proposed", "fixed", "wont_fix", "false_positive"];
const CATEGORIES: Category[] = ["functional", "browser", "api", "database", "security", "performance", "accessibility", "product"];

interface Props {
  /** Lock the list to one category (used by the Security / Performance / Accessibility screens). */
  category?: Category;
  projectId?: string;
  initialSeverity?: string;
}

export function FindingsTable({ category: fixedCategory, projectId, initialSeverity = "" }: Props) {
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState(initialSeverity);
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [offset, setOffset] = useState(0);
  const params = { q: search.trim(), severity, finding_status: status, category: fixedCategory ?? category, project_id: projectId, offset, limit: 25 };
  const findings = useQuery({ queryKey: ["findings", params], queryFn: () => api<Page<Finding>>(`/findings${query(params)}`) });
  const reset = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setOffset(0); };

  return (
    <>
      <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="finding-search" className="sr-only">Search findings</label>
          <Input id="finding-search" type="search" placeholder="Search title or component" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        </div>
        <div>
          <label htmlFor="finding-severity" className="sr-only">Severity</label>
          <Select id="finding-severity" value={severity} onChange={(e) => reset(setSeverity)(e.target.value)}>
            <option value="">All severities</option>
            {SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}
          </Select>
        </div>
        <div>
          <label htmlFor="finding-status" className="sr-only">Status</label>
          <Select id="finding-status" value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
          </Select>
        </div>
        {!fixedCategory && (
          <div>
            <label htmlFor="finding-category" className="sr-only">Category</label>
            <Select id="finding-category" value={category} onChange={(e) => reset(setCategory)(e.target.value)}>
              <option value="">All categories</option>
              {CATEGORIES.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
          </div>
        )}
      </div>
      <QueryView query={findings}>
        {(data) =>
          data.items.length === 0 ? (
            <Empty title="No findings match">
              Either no check in this area has failed, or no check has run yet. An empty list is not proof of quality: see the test results for what was actually executed.
            </Empty>
          ) : (
            <>
              <Table label="Findings" head={["Severity", "Finding", "Category", "Status", "Verification", "Confidence", "Tool", "Last seen"]}>
                {data.items.map((finding) => (
                  <tr key={finding.id}>
                    <td><SeverityBadge severity={finding.severity} /></td>
                    <td className="max-w-md">
                      <Link href={`/findings/${finding.id}`} className="font-medium underline-offset-2 hover:underline">{finding.title}</Link>
                      <span className="block truncate font-mono text-xs text-muted">{finding.affected_component}</span>
                    </td>
                    <td><Badge>{finding.category}</Badge></td>
                    <td><StateBadge state={finding.status} /></td>
                    <td><StateBadge state={finding.verification_status} /></td>
                    <td className="tabular-nums">{Math.round(finding.confidence * 100)}%</td>
                    <td className="text-muted">{finding.source_tool}</td>
                    <td className="whitespace-nowrap text-muted">{formatDate(finding.last_seen_at)}</td>
                  </tr>
                ))}
              </Table>
              <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
            </>
          )
        }
      </QueryView>
    </>
  );
}
