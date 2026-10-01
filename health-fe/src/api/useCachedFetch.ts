import { useEffect, useSyncExternalStore } from "react";

// A small shared cache for the Insights cards' non-default periods. Every
// card that asks for the same key shares one request and one result (a 90
// day /lastxh is ~26k readings, so it shouldn't be fetched per card). A
// result is reused for MAX_AGE_MS; after that, the next `tick` (App's
// refreshedAt) refetches it in the background while the old data stays on
// screen. Longer periods barely move minute to minute, so this refreshes
// them far less often than the 24h data App polls every minute.

const MAX_AGE_MS = 5 * 60_000;

interface Entry {
  data?: unknown;
  error: string | null;
  loading: boolean;
  fetchedAt: number;
}

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();

function setEntry(key: string, entry: Entry) {
  entries.set(key, entry);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Empties the cache; for tests. */
export function clearFetchCache() {
  entries.clear();
}

export interface Fetched<T> {
  data: T | null;
  // True only while there's nothing to show yet; a background refresh keeps
  // the previous data and doesn't set it.
  loading: boolean;
  error: string | null;
}

/**
 * Fetches `key` once and shares the result. A null key fetches nothing,
 * so callers can enable it conditionally without breaking hook order.
 */
export function useCachedFetch<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  tick: number
): Fetched<T> {
  const snapshot = () => (key === null ? undefined : entries.get(key));
  // Same snapshot for server rendering (the app renders client-side only,
  // but React requires one).
  const entry = useSyncExternalStore(subscribe, snapshot, snapshot);

  useEffect(() => {
    if (key === null) return;
    const current = entries.get(key);
    if (current?.loading) return;
    if (current && Date.now() - current.fetchedAt < MAX_AGE_MS) return;
    setEntry(key, {
      data: current?.data,
      error: null,
      loading: true,
      fetchedAt: current?.fetchedAt ?? 0,
    });
    fetcher().then(
      (data) => setEntry(key, { data, error: null, loading: false, fetchedAt: Date.now() }),
      (e: unknown) =>
        setEntry(key, {
          data: current?.data,
          error: e instanceof Error ? e.message : "Failed to fetch",
          loading: false,
          fetchedAt: Date.now(),
        })
    );
    // `fetcher` is a fresh closure every render; the key identifies the
    // request, so it alone (plus tick, for staleness) decides refetching.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  if (key === null) return { data: null, loading: false, error: null };
  const hasData = entry?.data !== undefined;
  return {
    data: hasData ? (entry!.data as T) : null,
    loading: !hasData && (entry === undefined || entry.loading),
    error: entry?.error ?? null,
  };
}
