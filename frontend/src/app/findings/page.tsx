"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { FindingsTable } from "@/components/findings-table";
import { Card, Loading, PageHeader } from "@/components/ui";

function FindingsContent() {
  const params = useSearchParams();
  return <FindingsTable projectId={params.get("project_id") ?? undefined} initialSeverity={params.get("severity") ?? ""} />;
}

export default function FindingsPage() {
  return (
    <>
      <PageHeader
        title="Bugs & findings"
        description="Each finding comes from a failed check and carries reproduction steps, evidence and a confidence value. Re-detection updates the same finding instead of creating a duplicate."
      />
      <Card>
        <Suspense fallback={<Loading />}>
          <FindingsContent />
        </Suspense>
      </Card>
    </>
  );
}
