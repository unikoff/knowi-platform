import { useEffect, useRef, useState } from 'react';
import { parsePublishedCatalog, type PublishedCatalog } from './catalog';

/** Core returns a version only after publication receipt is checked; failures stay closed. */
export function usePublishedCatalog(enabled: boolean, identityKey: string | null, courseId: string, baseUrl: string, policy: 'parallel' | 'cutover') {
  const scope = useRef({ signature: '', epoch: 0 });
  const signature = JSON.stringify([enabled, identityKey, courseId, baseUrl, policy]);
  if (scope.current.signature !== signature) scope.current = { signature, epoch: scope.current.epoch + 1 };
  const epoch = scope.current.epoch;
  const [state, setState] = useState<{ epoch: number; catalog: PublishedCatalog | null } | null>(null);
  useEffect(() => {
    if (!enabled || !identityKey) return;
    let stopped = false, attempts = 0, timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    const load = async () => {
      if (stopped) return;
      request = new AbortController();
      const active = request;
      const timeout = setTimeout(() => active.abort(), 10_000);
      const aborted = new Promise<never>((_, reject) => active.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
      try {
        const response = await Promise.race([fetch(`${baseUrl}/api/learning/analytics-catalog?course_id=${encodeURIComponent(courseId)}`, {
          method: 'GET', credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' }, signal: active.signal,
        }), aborted]);
        if (response.status === 401 || response.status === 403) {
          if (!stopped) setState({ epoch, catalog: null });
          return;
        }
        if (!response.ok) {
          const retry = response.status === 429 ? response.headers.get('Retry-After')?.trim() : undefined;
          const seconds = retry && /^\d+$/.test(retry) ? Number(retry) * 1000 : 0;
          const retryDate = retry && !/^\d+$/.test(retry) ? Date.parse(retry) : NaN;
          const serverDate = Date.parse(response.headers.get('Date') ?? '');
          const retryAfterMs = seconds || (Number.isFinite(retryDate) ? Number.isFinite(serverDate) ? Math.max(0, retryDate - serverDate) : Number.MAX_SAFE_INTEGER : 0);
          throw { retryAfterMs };
        }
        // Publication is usable only under the matching server-side policy.
        const policyReady = policy === 'parallel'
          ? response.headers.get('X-Analytics-Collection-Mode') === 'parallel'
          : response.headers.get('X-Analytics-Browser-Presence-Required') === 'true';
        if (!policyReady) {
          throw new Error('analytics collection policy unavailable');
        }
        const body: unknown = await Promise.race([response.json(), aborted]);
        if (stopped || active.signal.aborted) return;
        const catalog = parsePublishedCatalog(body, courseId);
        setState({ epoch, catalog });
        if (catalog) { attempts = 0; timer = setTimeout(() => { void load(); }, 60_000); }
        else throw new Error('catalog not published');
      } catch (error) {
        if (stopped) return;
        setState({ epoch, catalog: null });
        attempts++;
        if (attempts < 8) {
          const retryAfter = error && typeof error === 'object' && 'retryAfterMs' in error && typeof error.retryAfterMs === 'number' ? error.retryAfterMs : 0;
          const delay = Math.max(retryAfter, Math.min(60_000, 2000 * 2 ** (attempts - 1)) * (0.5 + Math.random() * 0.5));
          if (delay <= 2_147_000_000) timer = setTimeout(() => { void load(); }, delay);
        }
      } finally { clearTimeout(timeout); }
    };
    void load();
    return () => { stopped = true; request?.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [enabled, identityKey, courseId, baseUrl, policy, epoch]);
  return enabled && identityKey && state?.epoch === epoch ? state.catalog : null;
}
