"use client";

import { useQuery } from "@tanstack/react-query";

import { QueryView } from "@/components/query";
import { Card, StateBadge, Stat, Table } from "@/components/ui";
import { api } from "@/lib/api";
import type { Capabilities } from "@/lib/types";

/** Adapters, local tools, container images, Android devices and live host resources. */
export function CapabilitiesPanel() {
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/system/capabilities"),
    refetchInterval: 20_000,
  });
  return (
    <QueryView query={capabilities}>
      {(data) => (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Free RAM" value={`${data.resources.free_ram_gb} GB`} hint="the Android emulator needs about 3 GB" />
            <Stat label="Free disk" value={`${data.resources.free_disk_gb} GB`} />
            <Stat
              label="GPU memory"
              value={data.resources.gpu ? `${data.resources.gpu.vram_used_mb} / ${data.resources.gpu.vram_total_mb} MB` : "n/a"}
              hint={data.resources.gpu?.name ?? "nvidia-smi not found"}
            />
            <Stat
              label="Android devices"
              value={data.android_devices.length}
              hint={data.android_devices.map((d) => `${d.serial} (${d.state})`).join(", ") || "none connected; the emulator starts on demand"}
            />
          </div>
          <Card title={`Testing adapters (${data.adapters.length})`}>
            <p className="mb-2 text-muted">{data.concurrency}.</p>
            <Table label="Testing adapters" head={["Adapter", "Environment", "Needs", "Why not ready"]}>
              {data.adapters.map((adapter) => (
                <tr key={adapter.name}>
                  <td className="font-medium">{adapter.title}</td>
                  <td><StateBadge state={adapter.status} /></td>
                  <td className="text-muted">
                    {[adapter.needs_source && "source files", adapter.needs_target && "running target", ...adapter.requirements].filter(Boolean).join(", ") || "-"}
                  </td>
                  <td className="text-muted">{adapter.reasons.join("; ") || "-"}</td>
                </tr>
              ))}
            </Table>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Local tools">
              <Table label="Local tools" head={["Tool", "Purpose", "Status"]}>
                {data.tools.map((tool) => (
                  <tr key={tool.name}>
                    <td className="font-medium">{tool.name}</td>
                    <td className="text-muted">{tool.purpose}</td>
                    <td><StateBadge state={tool.installed ? "ready" : "unavailable"} /></td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="Container images">
              <Table label="Container images" head={["Image", "Status"]}>
                {data.images.map((image) => (
                  <tr key={image.image}>
                    <td className="font-mono text-xs">{image.image}</td>
                    <td><StateBadge state={image.present ? "ready" : "unavailable"} /></td>
                  </tr>
                ))}
              </Table>
            </Card>
          </div>
        </div>
      )}
    </QueryView>
  );
}
