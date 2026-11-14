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
  { value: "local_folder", label: "Source project (local folder)", hint: "Absolute path on this PC. The working tree is copied, including uncommitted changes; the original is never modified." },
  { value: "git", label: "Git repository (local clone or public GitHub / GitLab URL)", hint: "Shallow clone of the committed HEAD. No credentials are used, so only public remote repositories work." },
  { value: "zip", label: "ZIP upload", hint: "Extracted with path, size and compression-ratio checks." },
  { value: "url", label: "Website URL only", hint: "No source code: browser, header, performance and generic API checks apply." },
  { value: "openapi", label: "API / OpenAPI file", hint: "A .json, .yaml or .yml specification for contract tests." },
  { value: "apk", label: "Android APK upload", hint: "Static analysis, and dynamic tests on the emulator. The APK is never run on this PC itself." },
] as const;

const FILE_KINDS = ["zip", "openapi", "apk"] as const;
const ACCEPT: Record<string, string> = { zip: ".zip", openapi: ".json,.yaml,.yml", apk: ".apk" };

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(120),
    kind: z.enum(["local_folder", "git", "zip", "openapi", "url", "apk"]),
    description: z.string().max(2000),
    local_path: z.string().max(1024),
    repository: z.string().max(1024),
    target_url: z.string().max(2048),
    file: z.custom<FileList | undefined>(),
  })
  .superRefine((value, ctx) => {
    if (value.kind === "local_folder" && !value.local_path.trim()) {
      ctx.addIssue({ code: "custom", path: ["local_path"], message: "Folder path is required" });
    }
    if (value.kind === "git" && !value.repository.trim()) {
      ctx.addIssue({ code: "custom", path: ["repository"], message: "Repository path or URL is required" });
    }
    if ((FILE_KINDS as readonly string[]).includes(value.kind) && !value.file?.length) {
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
    defaultValues: { name: "", kind: "local_folder", description: "", local_path: "", repository: "", target_url: "" },
  });
  const kind = useWatch({ control: form.control, name: "kind" });
  const errors = form.formState.errors;
  const needsFile = (FILE_KINDS as readonly string[]).includes(kind);

  const importProject = useMutation({
    mutationFn: (values: FormValues) => {
      const body = new FormData();
      body.set("name", values.name.trim());
      body.set("kind", values.kind);
      body.set("description", values.description);
      if (values.kind === "local_folder") body.set("local_path", values.local_path.trim());
      if (values.kind === "git") body.set("repository", values.repository.trim());
      if (values.target_url.trim() && values.kind !== "apk") body.set("target_url", values.target_url.trim());
      const file = values.file?.[0];
      if (file && needsFile) body.set("file", file);
      return api<Project>("/projects/import", { method: "POST", body });
    },
    onSuccess: async (project) => {
      await client.invalidateQueries();
      router.push(`/projects/${project.id}`);
    },
  });

  return (
    <>
      <PageHeader title="Import application" description="Imported content is treated as untrusted: it is analysed statically and only ever executed inside sandbox containers or the Android emulator." />
      <Card className="max-w-2xl">
        <form className="space-y-4" noValidate onSubmit={form.handleSubmit((values) => importProject.mutate(values))}>
          <Field label="Project name" htmlFor="name" error={errors.name?.message}>
            <Input id="name" autoComplete="off" aria-invalid={!!errors.name} {...form.register("name")} />
          </Field>
          <Field label="What are you importing?" htmlFor="kind" hint={KINDS.find((k) => k.value === kind)?.hint}>
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
          {kind === "git" && (
            <Field label="Repository" htmlFor="repository" error={errors.repository?.message} hint="A local repository folder (C:\Projects\my-app) or https://github.com/owner/repo. Allowed hosts are set under Execution settings.">
              <Input id="repository" spellCheck={false} aria-invalid={!!errors.repository} {...form.register("repository")} />
            </Field>
          )}
          {needsFile && (
            <Field label={kind === "zip" ? "ZIP file" : kind === "apk" ? "APK file" : "Specification file"} htmlFor="file" error={errors.file?.message as string | undefined}>
              <Input id="file" type="file" accept={ACCEPT[kind]} aria-invalid={!!errors.file} {...form.register("file")} />
            </Field>
          )}
          {kind !== "apk" && (
            <Field
              label={kind === "url" ? "Target URL" : "Target URL (optional)"}
              htmlFor="target_url"
              error={errors.target_url?.message}
              hint={<>Where the application is running, for browser and API tests. The host must first be authorised under <Link href="/settings/execution" className="underline">Execution settings</Link>.</>}
            >
              <Input id="target_url" type="url" placeholder="http://127.0.0.1:8100" spellCheck={false} aria-invalid={!!errors.target_url} {...form.register("target_url")} />
            </Field>
          )}
          <Field label="Description (optional)" htmlFor="description" error={errors.description?.message}>
            <Input id="description" {...form.register("description")} />
          </Field>
          {kind === "apk" && <Notice>Android source projects (Gradle, Flutter, React Native) are detected when imported as a folder, ZIP or git repository, but are not built here. Upload the built APK to test the app.</Notice>}
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
