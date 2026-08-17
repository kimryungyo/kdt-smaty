import { useEffect, useRef, useState } from "react";

export type Polled<T> = {
  value: T | null;
  error: string | null;
  lastSuccessAt: number | null;
  refresh: () => Promise<void>;
};

export function useSnapshotPoll<T>(load: (signal: AbortSignal) => Promise<T>, intervalMs: number, enabled = true): Polled<T> {
  const [state, setState] = useState<Omit<Polled<T>, "refresh">>({ value: null, error: null, lastSuccessAt: null });
  const sequence = useRef(0);
  const refreshRef = useRef<() => Promise<void>>(async () => undefined);
  useEffect(() => {
    if (!enabled) { setState({ value: null, error: null, lastSuccessAt: null }); refreshRef.current = async () => undefined; return; }
    let alive = true;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort(); controller = new AbortController(); const mine = ++sequence.current;
      try { const value = await load(controller.signal); if (alive && mine === sequence.current) setState({ value, error: null, lastSuccessAt: Date.now() }); }
      catch (cause) { if (alive && mine === sequence.current && !(cause instanceof DOMException && cause.name === "AbortError")) setState((old) => ({ ...old, error: cause instanceof Error ? cause.message : "상태를 읽지 못했습니다." })); }
    };
    refreshRef.current = refresh;
    void refresh(); const timer = window.setInterval(() => void refresh(), intervalMs);
    return () => { alive = false; ++sequence.current; controller?.abort(); window.clearInterval(timer); refreshRef.current = async () => undefined; };
  }, [enabled, intervalMs, load]);
  return { ...state, refresh: () => refreshRef.current() };
}
