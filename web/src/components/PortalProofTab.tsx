import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Typography } from 'antd';
import { palette } from '../theme/tokens';
import { apiFetch } from '../services/auth';

const { Text } = Typography;

/** What the payer's own find-a-doctor portal said, captured live.
 *
 * A walk takes 40-210s against a real payer, so it cannot run inside the eligibility request:
 * the fast verdict renders first and this polls for the proof. It is started by a button, never
 * automatically — one lookup per provider is the volume boundary this system holds itself to.
 */

export interface PortalTarget {
  payer_key: string;
  npi: string;
  plan?: string | null;
  prior_network_status?: string | null;
  prior_source_url?: string | null;
  out_of_network_benefits?: boolean | null;
  group_contracted?: boolean | null;
  provider_first_name?: string | null;
  provider_last_name?: string | null;
  state?: string | null;
  city?: string | null;
  zip?: string | null;
  tin?: string | null;
}

interface Reconciled {
  network_status_before: string;
  network_status_after: string;
  changed: boolean;
  signal: { source: string; result: string; detail: string };
  determination: { code: string; label: string; reason: string };
}

interface CaptureStatus {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  reconciled?: Reconciled | null;
  error: string | null;
  verdict: string | null;
  portal_name: string | null;
  portal_url: string | null;
  note: string | null;
  screenshot: string | null;
  result_count: number | null;
  matched_name: string | null;
  /** Every network the portal named for this provider, not only the one searched. Empty means the
   *  portal was not asked or does not say — never "in no networks". */
  networks_accepted: string[];
  duration_ms: number | null;
}

const VERDICT_TONE: Record<string, { text: string; bg: string }> = {
  IN_NETWORK: { text: '#0f7b4f', bg: '#e6f6ee' },
  OUT_OF_NETWORK: { text: '#a8341f', bg: '#fdecea' },
  UNKNOWN: { text: palette.slate600, bg: palette.slate100 },
  BLOCKED: { text: '#8a6100', bg: '#fdf3dd' },
};

/** The driver appends "[portal walk: a → b → c]" to its note. Split it back out: the trail is the
 *  audit record of how the answer was reached, and reads better as steps than as prose. */
function splitNote(note: string | null): { prose: string; steps: string[] } {
  if (!note) return { prose: '', steps: [] };
  const m = note.match(/^([\s\S]*?)\s*\[portal walk:\s*([\s\S]+?)\]\s*$/);
  if (!m) return { prose: note, steps: [] };
  return { prose: m[1].trim(), steps: m[2].split('→').map((s) => s.trim()).filter(Boolean) };
}

export default function PortalProofTab({ target }: { target: PortalTarget | null }) {
  const [state, setState] = useState<CaptureStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [shotUrl, setShotUrl] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const poller = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (poller.current) window.clearInterval(poller.current);
    timer.current = null;
    poller.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  // The screenshot route is authenticated, and an <img src> cannot carry a bearer token — so
  // fetch it through apiFetch and hand the <img> an object URL instead. Revoked on replacement
  // and unmount so a long session does not leak blobs.
  useEffect(() => {
    const name = state?.status === 'done' ? state.screenshot : null;
    if (!name) return;
    let url: string | null = null;
    let cancelled = false;
    (async () => {
      const r = await apiFetch(`/portal/screenshot/${encodeURIComponent(name)}`);
      if (!r.ok || cancelled) return;
      url = URL.createObjectURL(await r.blob());
      setShotUrl(url);
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      setShotUrl(null);
    };
  }, [state?.status, state?.screenshot]);

  const start = useCallback(async () => {
    if (!target) return;
    setStarting(true);
    setFailed(null);
    setState(null);
    setElapsed(0);
    try {
      const res = await apiFetch('/portal/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...target, zip: target.zip ?? null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setFailed(body?.detail?.message ?? `Could not start the check (HTTP ${res.status}).`);
        return;
      }
      const { job_id } = await res.json();
      setState({ job_id, status: 'queued' } as CaptureStatus);
      timer.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);
      poller.current = window.setInterval(async () => {
        const r = await apiFetch(`/portal/capture/${job_id}`);
        if (!r.ok) return;
        const body: CaptureStatus = await r.json();
        setState(body);
        if (body.status === 'done' || body.status === 'error') stop();
      }, 2000);
    } finally {
      setStarting(false);
    }
  }, [target, stop]);

  if (!target) {
    return (
      <div style={styles.pad}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          A portal check needs a provider NPI and a payer. Run an eligibility check first.
        </Text>
      </div>
    );
  }

  // Mirrors the guard in portal/capture.py: ZIP *or* city *or* state is enough, because HealthSparq
  // searches on any of the three. Keep the two in step — a UI that allows less than the server does
  // hides usable checks, and one that allows more just re-creates the two-minute dead walk.
  const location = [target.zip, target.city, target.state].filter(Boolean).join(', ');
  const hasLocation = Boolean(target.zip || target.city || target.state);

  const running = state?.status === 'queued' || state?.status === 'running';
  const { prose, steps } = splitNote(state?.note ?? null);

  return (
    <div style={styles.pad}>
      <div style={styles.intro}>
        Drives the payer's own find-a-doctor portal and screenshots what it says. This is the
        member-facing directory, so it is the accuracy check on every other source — but it is only
        valid for the network it searched, which is why the plan from the 271 is pinned before the walk.
      </div>

      {!state && !failed && (
        <>
          <Button
            type="primary"
            onClick={start}
            loading={starting}
            disabled={!target.npi || !hasLocation}
          >
            Check the payer's portal
          </Button>
          <div style={styles.meta}>
            {target.payer_key} · NPI {target.npi}
            {target.plan ? ` · pinning “${target.plan}”` : ' · no plan given, so the walk can only return UNKNOWN'}
            {location ? ` · searching near ${location}` : ''}
          </div>
          {/* Every portal gates provider search behind a committed location. Starting without one
              spends up to two minutes and screenshots a portal that was never able to search —
              which reads like a real absence. Say what is missing instead. */}
          {!hasLocation && (
            <div style={styles.meta}>
              Add the clinic ZIP above to run a portal check — these directories cannot search
              without a location.
            </div>
          )}
        </>
      )}

      {running && (
        <div style={styles.panel} role="status" aria-live="polite">
          <div style={styles.panelHead}>
            <span style={styles.pulse} />
            <span style={styles.panelTitle}>Checking the payer's portal</span>
            <span style={styles.clock}>{elapsed}s</span>
          </div>
          <div style={styles.track}>
            <div style={styles.trackFill} />
          </div>
          <div style={styles.panelBody}>
            A real browser is walking {target.payer_key}'s directory for{' '}
            {target.provider_last_name ?? `NPI ${target.npi}`}
            {target.plan ? ` in “${target.plan}”` : ''}
            {location ? ` near ${location}` : ''}. Most walks take 50–120 seconds — UHC's five-step
            guest flow is at the long end and Cigna's can run past three minutes. The result is
            recorded either way.
          </div>
        </div>
      )}

      {failed && (
        <div style={styles.errorBox}>
          {failed} Check the payer key has a portal driver, then try again.
        </div>
      )}

      {state?.status === 'error' && (
        <div style={styles.errorBox}>
          The walk did not finish: {state.error}. Nothing was recorded for this attempt — try again,
          or fall back to the directory finding in the other tabs.
        </div>
      )}

      {state?.status === 'done' && (
        <div>
          <div style={styles.resultHead}>
            <span
              style={{
                ...styles.pill,
                color: (VERDICT_TONE[state.verdict ?? 'UNKNOWN'] ?? VERDICT_TONE.UNKNOWN).text,
                background: (VERDICT_TONE[state.verdict ?? 'UNKNOWN'] ?? VERDICT_TONE.UNKNOWN).bg,
              }}
            >
              {(state.verdict ?? 'UNKNOWN').replace(/_/g, ' ')}
            </span>
            <span style={styles.resultMeta}>
              {state.portal_name}
              {state.result_count != null ? ` · ${state.result_count} result(s)` : ''}
              {state.matched_name ? ` · matched ${state.matched_name}` : ''}
              {state.duration_ms ? ` · ${Math.round(state.duration_ms / 1000)}s` : ''}
            </span>
          </div>

          {/* The provider-first read. Worth its own block rather than a line of meta: for an OON it
              is what turns "no" into "no, and here is what they ARE in", and for an IN it is the
              answer for every other network this payer sells, from the one walk. */}
          {state.networks_accepted?.length > 0 && (
            <div style={styles.networksBox}>
              <div style={styles.networksHead}>
                {state.portal_name} lists this provider in {state.networks_accepted.length} network
                {state.networks_accepted.length === 1 ? '' : 's'}
              </div>
              <div style={styles.networkChips}>
                {state.networks_accepted.map((n) => (
                  <span key={n} style={styles.networkChip}>
                    {n}
                  </span>
                ))}
              </div>
              <div style={styles.networksFoot}>
                Read from the payer's own directory in this walk — so it answers for every network
                listed here, not only the one searched.
              </div>
            </div>
          )}

          {state.reconciled && (
            <div style={state.reconciled.changed ? styles.reconChanged : styles.reconSame}>
              <div style={styles.reconHead}>
                {state.reconciled.changed ? (
                  <>
                    Verdict updated
                    <span style={styles.reconArrow}>
                      {state.reconciled.network_status_before.replace(/_/g, ' ')} →{' '}
                      <strong>{state.reconciled.network_status_after.replace(/_/g, ' ')}</strong>
                    </span>
                  </>
                ) : (
                  <>Verdict unchanged · {state.reconciled.network_status_after.replace(/_/g, ' ')}</>
                )}
              </div>
              <div style={styles.reconBody}>{state.reconciled.signal.detail}</div>
              <div style={styles.reconDet}>
                Determination: <strong>{state.reconciled.determination.label}</strong> —{' '}
                {state.reconciled.determination.reason}
              </div>
            </div>
          )}

          {prose && <div style={styles.note}>{prose}</div>}

          {steps.length > 0 && (
            <div style={styles.trail}>
              <div style={styles.trailLabel}>How it got there</div>
              {steps.map((s, i) => (
                <div key={i} style={styles.step}>
                  <span style={styles.stepDot} />
                  {s}
                </div>
              ))}
            </div>
          )}

          {state.screenshot ? (
            <figure style={styles.figure}>
              {shotUrl ? (
                <img
                  src={shotUrl}
                  alt={`Screenshot of ${state.portal_name} showing the result for NPI ${target.npi}`}
                  style={styles.shot}
                />
              ) : (
                <div style={styles.shotSkeleton}>Loading the screenshot…</div>
              )}
              <figcaption style={styles.caption}>
                {state.portal_url && (
                  <a href={state.portal_url} target="_blank" rel="noreferrer" style={styles.link}>
                    Open the exact URL this was read from
                  </a>
                )}
              </figcaption>
            </figure>
          ) : (
            <div style={styles.meta}>No screenshot was captured for this walk.</div>
          )}

          <Button size="small" onClick={start} style={{ marginTop: 12 }}>
            Check again
          </Button>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  pad: { padding: '16px 18px' },
  intro: { color: palette.slate600, fontSize: 12, marginBottom: 12, maxWidth: 760, lineHeight: 1.6 },
  meta: { color: palette.slate400, fontSize: 11, marginTop: 8 },
  panel: {
    border: `1px solid ${palette.slate200}`,
    borderRadius: 10,
    padding: '14px 16px',
    background: '#fff',
    maxWidth: 760,
  },
  panelHead: { display: 'flex', alignItems: 'center', gap: 8 },
  panelTitle: { fontSize: 13, fontWeight: 600, color: palette.slate900 },
  clock: {
    marginLeft: 'auto',
    fontSize: 12,
    fontVariantNumeric: 'tabular-nums',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    color: palette.slate600,
  },
  pulse: {
    width: 8,
    height: 8,
    borderRadius: 999,
    background: palette.brand500,
    display: 'inline-block',
    animation: 'portalPulse 1.4s ease-in-out infinite',
  },
  track: { height: 3, borderRadius: 999, background: palette.slate100, overflow: 'hidden', margin: '10px 0' },
  trackFill: {
    height: '100%',
    width: '35%',
    borderRadius: 999,
    background: palette.brand500,
    animation: 'portalSlide 1.9s ease-in-out infinite',
  },
  panelBody: { color: palette.slate600, fontSize: 12, lineHeight: 1.6 },
  errorBox: {
    border: `1px solid #f0c4bd`,
    background: '#fdecea',
    color: '#a8341f',
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 12,
    maxWidth: 760,
  },
  resultHead: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' },
  pill: { fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 999 },
  resultMeta: { fontSize: 11, color: palette.slate400 },
  networksBox: {
    border: `1px solid ${palette.slate200}`,
    borderRadius: 8,
    padding: '12px 14px',
    marginBottom: 14,
    maxWidth: 760,
    background: palette.slate100,
  },
  networksHead: { fontSize: 12, fontWeight: 600, color: palette.slate700, marginBottom: 8 },
  networkChips: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  networkChip: {
    fontSize: 11,
    padding: '3px 8px',
    borderRadius: 999,
    background: '#fff',
    border: `1px solid ${palette.slate200}`,
    color: palette.slate700,
  },
  networksFoot: { fontSize: 11, color: palette.slate400, marginTop: 8, lineHeight: 1.5 },
  note: { color: palette.slate700, fontSize: 12, lineHeight: 1.65, maxWidth: 760, marginBottom: 12 },
  trail: {
    borderLeft: `2px solid ${palette.slate200}`,
    paddingLeft: 12,
    marginBottom: 14,
    maxWidth: 760,
  },
  trailLabel: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.4px',
    textTransform: 'uppercase',
    color: palette.slate400,
    marginBottom: 6,
  },
  step: { fontSize: 11.5, color: palette.slate600, padding: '2px 0', display: 'flex', alignItems: 'baseline', gap: 8 },
  stepDot: { width: 4, height: 4, borderRadius: 999, background: palette.slate300, display: 'inline-block', flex: 'none' },
  figure: { margin: 0, maxWidth: 760 },
  shot: {
    width: '100%',
    maxWidth: '100%',
    border: `1px solid ${palette.slate200}`,
    borderRadius: 8,
    display: 'block',
  },
  reconChanged: {
    borderLeft: `3px solid ${palette.brand500}`,
    background: palette.brand50,
    borderRadius: 8,
    padding: '10px 14px',
    marginBottom: 12,
    maxWidth: 760,
  },
  reconSame: {
    borderLeft: `3px solid ${palette.slate300}`,
    background: palette.slate100,
    borderRadius: 8,
    padding: '10px 14px',
    marginBottom: 12,
    maxWidth: 760,
  },
  reconHead: { fontSize: 12, fontWeight: 600, color: palette.slate900, display: 'flex', gap: 8, flexWrap: 'wrap' },
  reconArrow: { fontWeight: 400, color: palette.slate600 },
  reconBody: { fontSize: 12, color: palette.slate600, marginTop: 4, lineHeight: 1.6 },
  reconDet: { fontSize: 12, color: palette.slate700, marginTop: 6, lineHeight: 1.6 },
  shotSkeleton: {
    border: `1px dashed ${palette.slate300}`,
    borderRadius: 8,
    padding: '48px 16px',
    textAlign: 'center',
    color: palette.slate400,
    fontSize: 12,
    background: palette.slate100,
  },
  caption: { fontSize: 11, color: palette.slate400, marginTop: 6 },
  link: { color: palette.brand500 },
};
