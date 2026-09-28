"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";

export type Resource<T> = {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refresh: () => Promise<void>;
};

export function useResource<T>(path: string | null): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path));

  const refresh = useCallback(async () => {
    if (!path) return;
    try {
      const next = await api.get<T>(path);
      setData(next);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : null);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(true);
    void refresh();
    const onChange = () => void refresh();
    // Identity switches and committed server events both refresh every visible resource.
    window.addEventListener("aitc:identity", onChange);
    window.addEventListener("aitc:refresh", onChange);
    return () => {
      window.removeEventListener("aitc:identity", onChange);
      window.removeEventListener("aitc:refresh", onChange);
    };
  }, [refresh]);

  return { data, error, loading, refresh };
}

/** Poll the durable event cursor; call onChange when committed events arrive (FR-23, AT-18). */
export function useEventCursor(projectId: string | null, onChange: () => void, intervalMs = 3000) {
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    if (!projectId) return;
    let cursor: number | null = null;
    let stopped = false;
    const tick = async () => {
      try {
        const page = await api.get<{ items: unknown[]; next_cursor: number }>(
          `/projects/${projectId}/events?after=${cursor ?? 0}&limit=100`,
        );
        if (cursor !== null && page.items.length > 0) onChange();
        cursor = page.next_cursor;
        setConnected(true);
        setLastSync(new Date());
      } catch {
        setConnected(false);
      }
    };
    void tick();
    const id = window.setInterval(() => !stopped && void tick(), intervalMs);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [projectId, onChange, intervalMs]);

  return { lastSync, connected };
}
