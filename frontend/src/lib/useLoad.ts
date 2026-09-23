import { useCallback, useEffect, useState } from "react";

/** Load once on mount; expose the error message instead of leaving the screen blank. */
export function useLoad<T>(fn: () => Promise<T>): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    fn().then(
      (d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      },
      (e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n]);
  const reload = useCallback(() => setN((v) => v + 1), []);
  return { data, error, reload };
}
