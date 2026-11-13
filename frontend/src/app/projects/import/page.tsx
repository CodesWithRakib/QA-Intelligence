"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Button, Card, ErrorNote, Field, Input, Notice, PageHeader, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";

const KINDS = [
  { value: "local_folder", label: "Local folder", hint: "Absolute path on this PC. Files are copied; the original is never modified." },
  { value: "zip", label: "ZIP upload", hint: "Extracted with path, size and compression-ratio checks." },
  { value: "openapi", label: "OpenAPI / Swagger file", hint: "A .json, .yaml or .yml specification." },
  { value: "url", label: "Running website or API (URL only)", hint: "No source code: only browser, API and header checks apply." },
] as const;

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(120),
    kind: z.enum(["local_folder", "zip", "openapi", "url"]),
    description: z.string().max(2000),
    local_path: z.string().max(1024),
    target_url: z.string().max(2048),
    file: z.custom<FileList | undefined>(),
  })
  .superRefine((value, ctx) => {
    if (value.kind === "local_folder" && !value.local_path.trim()) {
      ctx.addIssue({ code: "custom", path: ["local_path"], message: "Folder path is required" });
    }
    if ((value.kind === "zip" || value.kind === "openapi") && !value.file?.length) {
      ctx.addIssue({ code: "custom", path: ["file"], message: "Choose a file" });
    }
    if (value.kind === "url" && !value.target_url.trim()) {
      ctx.addIssue({ code: "custom", path: ["target_url"], message: "URL is required" });
    }
    if (value.target_url.trim() && !/^https?:\/\/.+/i.test(value.target_url.trim())) {
      ctx.addIssue({ code: "custom", path: ["target_url"], message: "Must start with http:// or https://" });
    }
  });

type FormValues = z.infer<typeof schema>;

export default function ImportProjectPage() {
  const router = useRouter();
  const client = useQueryClient();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", kind: "local_folder", description: "", local_path: "", target_url: "" },
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const errors = form.formState.errors;

  const importProject = useMutation({
    mutationFn: (values: FormValues) => {
      const body = new FormData();
      body.set("name", values.name.trim());
      body.set("kind", values.kind);
      body.set("description", values.description);
      if (values.kind === "local_folder") body.set("local_path", values.local_path.trim());
      if (values.target_url.trim()) body.set("target_url", values.target_url.trim());
      const file = values.file?.[0];
      if (file && (values.kind === "zip" || values.kind === "openapi")) body.set("file", file);
      return api<Project>("/projects/import", { method: "POST", body });
    },
    onSuccess: async (project) => {
      await client.invalidateQueries();
      router.push(`/projects/${project.id}`);
    },
  });

  return (
    <>
      <PageHeader title="Import project" description="Imported content is treated as untrusted: it is analysed statically and only ever executed inside sandbox containers." />
      <Card className="max-w-2xl">
        <form className="space-y-4" noValidate onSubmit={form.handleSubmit((values) => importProject.mutate(values))}>
          <Field label="Project name" htmlFor="name" error={errors.name?.message}>
            <Input id="name" autoComplete="off" aria-invalid={!!errors.name} {...form.register("name")} />
          </Field>
          <Field label="Source type" htmlFor="kind" hint={KINDS.find((k) => k.value === kind)?.hint}>
            <Select id="kind" {...form.register("kind")}>
              {KINDS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </Select>
          </Field>
          {kind === "local_folder" && (
            <Field label="Folder path" htmlFor="local_path" error={errors.local_path?.message} hint="Example: C:\Projects\my-app. node_modules, .git, build output and secret files (.env, *.pem) are not copied.">
              <Input id="local_path" spellCheck={false} aria-invalid={!!errors.local_path} {...form.register("local_path")} />
            </Field>
          )}
          {(kind === "zip" || kind === "openapi") && (
            <Field label={kind === "zip" ? "ZIP file" : "Specification file"} htmlFor="file" error={errors.file?.message as string | undefined}>
              <Input id="file" type="file" accept={kind === "zip" ? ".zip" : ".json,.yaml,.yml"} aria-invalid={!!errors.file} {...form.register("file")} />
            </Field>
          )}
          <Field
            label={kind === "url" ? "Target URL" : "Target URL (optional)"}
            htmlFor="target_url"
            error={errors.target_url?.message}
            hint={<>Where the application is running, for browser and API tests. The host must first be authorised under <Link href="/settings/execution" className="underline">Execution settings</Link>.</>}
          >
            <Input id="target_url" type="url" placeholder="http://127.0.0.1:8100" spellCheck={false} aria-invalid={!!errors.target_url} {...form.register("target_url")} />
          </Field>
          <Field label="Description (optional)" htmlFor="description" error={errors.description?.message}>
            <Input id="description" {...form.register("description")} />
          </Field>
          <Notice>Git, GitHub and GitLab imports are not implemented yet. Clone the repository locally and import the folder, or upload a ZIP export.</Notice>
          {importProject.isError && <ErrorNote error={importProject.error} />}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={importProject.isPending}>
              {importProject.isPending ? "Importing and analysing…" : "Import and analyse"}
            </Button>
            <Button onClick={() => router.push("/projects")}>Cancel</Button>
          </div>
        </form>
      </Card>
    </>
  );
}
