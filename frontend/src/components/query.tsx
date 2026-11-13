"use client";

import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ErrorNote, Loading } from "@/components/ui";
import { api } from "@/lib/api";

/** Renders the loading and error states once, so every screen handles them the same way. */
export function QueryView<T>({ query, children }: { query: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorNote error={query.error} retry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}

/** POST/PUT/PATCH helper that refreshes every cached query on success. */
export function useAction<TBody = void, TResult = unknown>(method: "POST" | "PUT" | "PATCH", path: string | ((body: TBody) => string)) {
  const client = useQueryClient();
  return useMutation<TResult, Error, TBody>({
    mutationFn: (body) =>
      api<TResult>(typeof path === "function" ? path(body) : path, { method, json: body === undefined ? undefined : body }),
    onSuccess: () => client.invalidateQueries(),
  });
}

/** Poll while background work (runs, repairs, AI jobs) is in progress. */
export function pollWhile<T>(active: (data: T) => boolean, intervalMs = 3000) {
  return (state: { state: { data: T | undefined } }) => (state.state.data && active(state.state.data) ? intervalMs : false);
}
