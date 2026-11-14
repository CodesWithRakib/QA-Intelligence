import "server-only";

import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Server-side configuration lookup: process environment first, then the repository-level `.env`
 * (one directory above the frontend). Read once per process; values are never sent to the browser.
 */
let fileValues: Record<string, string> | null = null;

function loadFile(): Record<string, string> {
  const values: Record<string, string> = {};
  try {
    const text = readFileSync(path.join(process.cwd(), "..", ".env"), "utf8").replace(/^﻿/, "");
    for (const line of text.split(/\r?\n/)) {
      const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$/.exec(line);
      if (match?.[1]) values[match[1]] = (match[2] ?? "").trim();
    }
  } catch {
    // No .env file: only the process environment is used.
  }
  return values;
}

export function serverEnv(name: string): string | undefined {
  const fromProcess = process.env[name];
  if (fromProcess) return fromProcess;
  fileValues ??= loadFile();
  return fileValues[name] || undefined;
}
