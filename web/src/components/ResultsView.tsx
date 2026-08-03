import { Table, Card, Typography, Divider, Tabs } from 'antd';
import type { TableColumnsType } from 'antd';
import { palette } from '../theme/tokens';
import PortalProofTab, { type Determination, type PortalTarget, type Reconciled } from './PortalProofTab';

const { Text } = Typography;

export interface Benefit {
  service_type: string;
  service_type_label: string;
  network: 'IN' | 'OON';
  category: 'copay' | 'coinsurance' | 'deductible' | 'oop_max' | 'limitation';
  level: string;
  amount: number | null;
  percent: number | null;
  time_period: string | null;
  met: number | null;
  remaining: number | null;
}

export interface CorroborationSignal {
  source: string;
  result: string;
  detail: string;
}

export interface NetworkVerdict {
  status: string;
  matched_provider: Record<string, unknown> | null;
  plan_or_network_checked: string;
  source_url: string;
  confidence: string;
  notes: string;
  corroboration: CorroborationSignal[] | null;
  evidence: Record<string, unknown> | null;
}

export interface PlanCandidate {
  plan: string;
  is_product: boolean;
  rank: number;
}

export interface EvidenceSource {
  source: string;
  answers: string;
  status: string;
  tone: 'success' | 'warning' | 'danger' | 'neutral';
  detail: string;
}

export interface EligibilityResponse {
  request_id: string;
  coverage_active: boolean | null;
  plan_name: string | null;
  group: string | null;
  network_status: 'IN_NETWORK' | 'OUT_OF_NETWORK' | 'REVIEW' | 'UNKNOWN';
  benefits: Benefit[];
  pcp_required: boolean | null;
  prior_auth_required: boolean | null;
  referral_required: boolean | null;
  cob: boolean | null;
  network_verdict: NetworkVerdict | null;
  corroboration: CorroborationSignal[] | null;
  plan_candidates: PlanCandidate[];
  selected_plan: string | null;
  stedi_network_status: string | null;
  out_of_network_benefits: boolean | null;
  plan_oon_capability: boolean | null;
  /** The member's residence ZIP from the 271 — pins the plan list on portals that scope it by
   *  where the member lives. See PortalTarget.member_zip. */
  member_zip: string | null;
  determination: Determination | null;
  evidence_sources?: EvidenceSource[];
  source_audit?: { source?: string; note?: string; error_codes?: string[] } | null;
}

type Tone = 'success' | 'warning' | 'danger' | 'neutral';

interface MatrixRow {
  key: string;
  service_type_label: string;
  category: string;
  level: string;
  time_period: string | null;
  in_value: string;
  in_met: number | null;
  in_remaining: number | null;
  oon_value: string;
  oon_met: number | null;
  oon_remaining: number | null;
}

function formatBenefitValue(b: Benefit): string {
  if (b.category === 'coinsurance') return b.percent != null ? `${b.percent}%` : '-';
  if (b.amount != null) return `$${b.amount.toLocaleString()}`;
  return '-';
}

function buildMatrix(benefits: Benefit[]): MatrixRow[] {
  const map = new Map<string, MatrixRow>();
  for (const b of benefits) {
    const rowKey = `${b.service_type_label}|${b.category}|${b.level ?? ''}|${b.time_period ?? ''}`;
    if (!map.has(rowKey)) {
      map.set(rowKey, {
        key: rowKey,
        service_type_label: b.service_type_label,
        category: b.category,
        level: b.level ?? '',
        time_period: b.time_period,
        in_value: '-',
        in_met: null,
        in_remaining: null,
        oon_value: '-',
        oon_met: null,
        oon_remaining: null,
      });
    }
    const row = map.get(rowKey)!;
    if (b.network === 'IN') {
      row.in_value = formatBenefitValue(b);
      row.in_met = b.met;
      row.in_remaining = b.remaining;
    } else {
      row.oon_value = formatBenefitValue(b);
      row.oon_met = b.met;
      row.oon_remaining = b.remaining;
    }
  }
  return Array.from(map.values());
}

function networkStatusTone(status: string): Tone {
  if (status === 'IN_NETWORK') return 'success';
  if (status === 'OUT_OF_NETWORK') return 'danger';
  if (status === 'REVIEW') return 'warning';
  return 'neutral';
}

/** Provider-network status as a committed direction — never a blank "UNKNOWN".
 *
 * A raw UNKNOWN tells the person working the account nothing; they have to bill something either
 * way. So an unsettled row falls back to the determination's committed reading (`display_code`,
 * computed server-side in domain/determination.py) and says out loud that it is low confidence.
 * `network_status` itself is untouched — this is presentation only, and the Sources tab still
 * shows what each source independently said.
 */
function committedNetwork(
  status: string,
  determination: Determination | null,
): { text: string; tone: Tone; unsettled: boolean } {
  if (status !== 'UNKNOWN') {
    return { text: status.replace(/_/g, ' '), tone: networkStatusTone(status), unsettled: false };
  }
  const lean = determination?.display_code;
  // No member, no network question — say that rather than commit to a direction. See
  // domain/determination._not_established.
  if (lean === 'NOT_ESTABLISHED') {
    return { text: 'NOT ESTABLISHED', tone: 'neutral', unsettled: false };
  }
  if (!lean || lean === 'UNKNOWN') return { text: 'NOT ESTABLISHED', tone: 'neutral', unsettled: false };
  // Every OON flavour (payer-level, physician, with-benefits) is out-of-network on this axis; the
  // distinction between them belongs to the Determination tile, not to provider-network status.
  const inn = lean === 'IN_NETWORK';
  // Amber in BOTH directions, deliberately. Green reads as "confirmed in-network", and a green
  // low-confidence lean is precisely the false IN this system exists to remove — the colour has to
  // carry the caveat as loudly as the meter does.
  //
  // The text stays bare: in a tile the meter states the strength, and saying it twice gives the
  // tile two things to keep in sync. `unsettled` lets prose contexts, which have no meter, say it.
  return { text: inn ? 'IN NETWORK' : 'OUT OF NETWORK', tone: 'warning', unsettled: true };
}

/** Headline for the directory finding. A source that could not answer says so in its notes, which
 *  are rendered directly underneath — the headline shows the committed reading instead of the word
 *  UNKNOWN, which tells the reader nothing they can act on. */
function verdictHeadline(v: NetworkVerdict, committed: { text: string; unsettled: boolean }): string {
  // Prose, not a tile — there is no meter here, so the strength is spelled out.
  if (v.status === 'UNKNOWN') {
    return committed.unsettled ? `${committed.text} · low confidence` : committed.text;
  }
  return `${v.status.replace(/_/g, ' ')} · confidence: ${v.confidence}`;
}

/** Tone for a determination, downgraded to amber whenever the reading is a lean rather than a
 *  finding. Same reason as above: only a confirmed verdict earns a decisive colour. */
function committedDeterminationTone(d: Determination | null): Tone {
  if (!d) return 'neutral';
  // "Eligibility not established" is not a verdict of any colour — it reports that the check did
  // not run, so it must not borrow the amber a real low-confidence reading earns.
  if (d.display_code === 'NOT_ESTABLISHED' || d.confidence === 'none') return 'neutral';
  if (d.confidence === 'low') return 'warning';
  return determinationTone(d.display_code ?? d.code);
}

function determinationTone(code: string | undefined): Tone {
  if (code === 'IN_NETWORK') return 'success';
  if (code === 'OUT_OF_NETWORK') return 'danger';
  // physician-OON and OON-with-benefits are distinct, still-actionable states → amber, not red
  if (code === 'OUT_OF_NETWORK_WITH_BENEFITS' || code === 'PHYSICIAN_OUT_OF_NETWORK' || code === 'REVIEW')
    return 'warning';
  return 'neutral';
}

function tinScopeTone(result: string): Tone {
  if (result === 'corroborates') return 'success';
  if (result === 'contradicts') return 'danger';
  return 'neutral';
}

const TONE_COLORS: Record<Tone, { text: string; bg: string }> = {
  success: { text: palette.success, bg: palette.successBg },
  warning: { text: palette.warning, bg: palette.warningBg },
  danger: { text: palette.danger, bg: palette.dangerBg },
  neutral: { text: palette.slate500, bg: palette.slate100 },
};

const CONFIDENCE_STEPS: Record<string, number> = { low: 1, medium: 2, high: 3 };

/** How strongly a reading is held, as a 3-segment meter.
 *
 * A strength is a quantity, so it reads faster as a bar than as the words "(low confidence)" buried
 * in the label — and it keeps the label free to say only what the determination IS. The filled
 * segments take the tile's own tone so a low-confidence reading cannot look decisive.
 */
function ConfidenceMeter({ confidence, tone }: { confidence: string; tone: Tone }) {
  const steps = CONFIDENCE_STEPS[confidence] ?? 0;
  if (!steps) return null;
  return (
    <div style={styles.meterRow}>
      <div
        style={styles.meter}
        role="meter"
        aria-valuenow={steps}
        aria-valuemin={1}
        aria-valuemax={3}
        aria-label={`Confidence: ${confidence}`}
      >
        {[1, 2, 3].map((i) => (
          <span
            key={i}
            style={{
              ...styles.meterSeg,
              background: i <= steps ? TONE_COLORS[tone].text : palette.slate200,
            }}
          />
        ))}
      </div>
      <span style={styles.meterText}>{confidence}</span>
    </div>
  );
}

/** A network tile before the portal has been asked. Deliberately says nothing about direction —
 *  a placeholder that guessed would be the very thing the gate exists to prevent. */
function PendingTile({ label }: { label: string }) {
  return (
    <div style={styles.statTile}>
      <div style={styles.statLabel}>{label}</div>
      <span style={styles.pendingPill}>PENDING</span>
      <div style={styles.pendingNote}>Run the portal check to settle this.</div>
    </div>
  );
}

function StatTile({
  label,
  value,
  tone,
  confidence,
}: {
  label: string;
  value: string;
  tone: Tone;
  confidence?: string;
}) {
  const c = TONE_COLORS[tone];
  return (
    <div style={styles.statTile}>
      <div style={styles.statLabel}>{label}</div>
      <span style={{ ...styles.statPill, color: c.text, background: c.bg }}>{value}</span>
      {confidence && <ConfidenceMeter confidence={confidence} tone={tone} />}
    </div>
  );
}

function CostCell({ value, met, remaining }: { value: string; met: number | null; remaining: number | null }) {
  return (
    <div>
      <div style={{ fontWeight: 600 }}>{value}</div>
      {met != null && <div style={{ fontSize: 11, color: palette.slate400, marginTop: 2 }}>Met: ${met.toLocaleString()}</div>}
      {remaining != null && <div style={{ fontSize: 11, color: palette.slate400 }}>Rem: ${remaining.toLocaleString()}</div>}
    </div>
  );
}

function TabLabel({ tone, text }: { tone: Tone; text: string }) {
  return (
    <span style={styles.tabLabel}>
      <span style={{ ...styles.tabDot, background: TONE_COLORS[tone].text }} />
      {text}
    </span>
  );
}

const matrixColumns: TableColumnsType<MatrixRow> = [
  { title: 'Service Type', dataIndex: 'service_type_label', key: 'service_type_label', width: 200, ellipsis: true },
  {
    title: 'Category',
    dataIndex: 'category',
    key: 'category',
    width: 120,
    render: (v: string) => v.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
  },
  { title: 'Level', dataIndex: 'level', key: 'level', width: 100 },
  { title: 'Period', dataIndex: 'time_period', key: 'time_period', width: 100, render: (v: string | null) => v ?? '-' },
  {
    title: 'In-Network',
    key: 'in',
    width: 150,
    render: (_: unknown, row: MatrixRow) => <CostCell value={row.in_value} met={row.in_met} remaining={row.in_remaining} />,
  },
  {
    title: 'Out-of-Network',
    key: 'oon',
    width: 150,
    render: (_: unknown, row: MatrixRow) => <CostCell value={row.oon_value} met={row.oon_met} remaining={row.oon_remaining} />,
  },
];

export default function ResultsView({
  result,
  portalTarget = null,
  onPortalReconciled,
  onPortalFinished,
  portalChecked = false,
}: {
  result: EligibilityResponse | null;
  /** Provider + clinic only. The member's name must never reach a payer portal, so it is
   *  deliberately absent here — the server resolves the provider's name from NPPES by NPI. */
  portalTarget?: PortalTarget | null;
  /** Fired when a finished portal walk reconciles the standing verdict, so the summary tiles
   *  above the tabs move with it instead of contradicting the tab below them. */
  onPortalReconciled?: (r: Reconciled) => void;
  /** Fired when a walk reaches a terminal state, whatever it said. */
  onPortalFinished?: () => void;
  /** Has a portal walk finished for this result? The network verdict is withheld until it has.
   *  The portal is the member-facing directory and therefore the accuracy check on every other
   *  source — showing a determination before it has spoken invites acting on the weaker read. */
  portalChecked?: boolean;
}) {
  if (!result) {
    return (
      <div style={styles.emptyState}>
        <Text type="secondary">Run a check to see coverage, network status, and cost-share details.</Text>
      </div>
    );
  }

  // Provider-network provenance: the resolver emits source "TIC" (credentialing/TiC short-circuit);
  // the directory-corroboration path emits "TIN-scope". Surface whichever is present.
  const tinSignal = result.corroboration?.find((s) => s.source === 'TIC' || s.source === 'TIN-scope') ?? null;
  const tinTone: Tone = tinSignal ? tinScopeTone(tinSignal.result) : 'neutral';
  const network = committedNetwork(result.network_status, result.determination);
  const networkTone = network.tone;
  const costShareTone: Tone = result.benefits.length > 0 ? 'success' : 'neutral';

  const evidence = result.evidence_sources ?? [];
  const evidenceColumns: TableColumnsType<EvidenceSource> = [
    {
      title: 'Source',
      dataIndex: 'source',
      key: 'source',
      width: 180,
      render: (v: string, row: EvidenceSource) => (
        <div>
          <div style={{ fontWeight: 600 }}>{v}</div>
          <div style={{ fontSize: 11, color: palette.slate400 }}>answers: {row.answers}</div>
        </div>
      ),
    },
    {
      title: 'Finding',
      dataIndex: 'status',
      key: 'status',
      width: 150,
      render: (v: string, row: EvidenceSource) => (
        <span style={{ ...styles.statPill, color: TONE_COLORS[row.tone].text, background: TONE_COLORS[row.tone].bg }}>
          {v.replace(/_/g, ' ')}
        </span>
      ),
    },
    { title: 'Detail', dataIndex: 'detail', key: 'detail', render: (v: string) => <span style={{ fontSize: 12 }}>{v}</span> },
  ];

  const portalTone: Tone =
    result.network_verdict?.status === 'IN_NETWORK'
      ? 'success'
      : result.network_verdict?.status === 'OUT_OF_NETWORK'
        ? 'danger'
        : 'neutral';

  const tabItems = [
    {
      key: 'portal',
      label: <TabLabel tone={portalTone} text="Portal proof" />,
      children: (
        <PortalProofTab
          target={portalTarget}
          onReconciled={onPortalReconciled}
          onFinished={onPortalFinished}
        />
      ),
    },

    {
      key: 'sources',
      label: <TabLabel tone={committedDeterminationTone(result.determination)} text="Sources" />,
      children: (
        <div style={styles.tabPad}>
          <div style={{ ...styles.verdictBody, marginBottom: 10, fontSize: 12 }}>
            Each source answers independently. The determination combines the <b>provider-network</b> signal
            (credentialing → TiC → directory) with the <b>plan tier</b> from the Stedi 271.
          </div>
          {evidence.length > 0 ? (
            <Table size="small" pagination={false} rowKey="source" dataSource={evidence} columns={evidenceColumns} />
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>No source breakdown for this result.</Text>
          )}
          <div style={{ marginTop: 14, display: portalChecked ? undefined : 'none' }}>
            <span style={styles.statLabel}>Calculated determination</span>
            <div style={{ marginTop: 4 }}>
              <span
                style={{
                  ...styles.statPill,
                  color: TONE_COLORS[committedDeterminationTone(result.determination)].text,
                  background: TONE_COLORS[committedDeterminationTone(result.determination)].bg,
                }}
              >
                {result.determination?.display_label ?? result.determination?.label ?? network.text}
              </span>
            </div>
            {result.determination?.reason && (
              <div style={{ ...styles.verdictBody, marginTop: 6, fontSize: 12 }}>{result.determination.reason}</div>
            )}
            {/* A low-confidence reading is a lean, so the evidence behind it and the one thing that
                would settle it are the actionable part — without them the tile is just an opinion. */}
            {result.determination?.confidence !== 'high' && result.determination?.basis && (
              <div style={{ ...styles.verdictBody, marginTop: 6, fontSize: 12 }}>
                <strong>Why:</strong> {result.determination.basis}
              </div>
            )}
            {result.determination?.confidence !== 'high' && result.determination?.next_step && (
              <div style={{ ...styles.verdictBody, marginTop: 4, fontSize: 12 }}>
                <strong>To confirm:</strong> {result.determination.next_step}
              </div>
            )}
          </div>
        </div>
      ),
    },
    {
      key: 'network',
      label: <TabLabel tone={networkTone} text="Network Finding" />,
      children: (
        <div style={styles.tabPad}>
          {result.network_verdict ? (
            <>
              <div style={styles.verdictTitle}>
                {verdictHeadline(result.network_verdict, network)}
              </div>
              {result.network_verdict.notes && <div style={{ ...styles.verdictBody, marginTop: 6 }}>{result.network_verdict.notes}</div>}
              {result.network_verdict.source_url &&
                (/^https?:\/\//.test(result.network_verdict.source_url) ? (
                  <div style={{ marginTop: 8 }}>
                    <a href={result.network_verdict.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                      View source
                    </a>
                  </div>
                ) : (
                  <div style={{ ...styles.verdictBody, marginTop: 8, fontSize: 12 }}>
                    Source: {result.network_verdict.source_url}
                  </div>
                ))}
            </>
          ) : result.source_audit?.note ? (
            <>
              <div style={styles.verdictTitle}>
                {result.source_audit.source === 'stedi-271' ? 'Stedi 270/271' : 'No result'}
                {result.source_audit.error_codes?.length ? ` · AAA ${result.source_audit.error_codes.join(', ')}` : ''}
              </div>
              <div style={{ ...styles.verdictBody, marginTop: 6 }}>{result.source_audit.note}</div>
            </>
          ) : (
            <Text type="secondary">No additional network finding for this case.</Text>
          )}
        </div>
      ),
    },
    {
      key: 'tin',
      label: <TabLabel tone={tinTone} text="TiC / TIN" />,
      children: (
        <div style={styles.tabPad}>
          {tinSignal ? (
            <div>
              <span style={{ ...styles.statPill, color: TONE_COLORS[tinTone].text, background: TONE_COLORS[tinTone].bg }}>
                {tinSignal.result === 'n/a' ? 'N/A' : tinSignal.result.toUpperCase()}
              </span>
              <div style={{ ...styles.verdictBody, marginTop: 6 }}>{tinSignal.detail}</div>
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              No billing TIN provided — enter the billing TIN to check the provider’s network status
              via Transparency-in-Coverage (commercial) or the clinic credentialing matrix.
            </Text>
          )}
        </div>
      ),
    },
    {
      key: 'cost',
      label: <TabLabel tone={costShareTone} text="Cost-Share" />,
      children:
        result.benefits.length === 0 ? (
          <div style={styles.tabPad}>
            <Text type="secondary">No benefit details returned for this member.</Text>
          </div>
        ) : (
          <>
            <div style={styles.legendRow}>
              <span style={styles.legendItem}>
                <span style={{ ...styles.legendDot, background: palette.success }} />
                In-Network
              </span>
              <span style={styles.legendItem}>
                <span style={{ ...styles.legendDot, background: palette.danger }} />
                Out-of-Network
              </span>
            </div>
            <Table<MatrixRow>
              columns={matrixColumns}
              dataSource={buildMatrix(result.benefits)}
              pagination={{ pageSize: 20, showSizeChanger: false }}
              size="small"
              scroll={{ x: 820 }}
              onRow={(_, index) => ({ style: index != null && index % 2 === 1 ? { background: '#FAFBFC' } : {} })}
            />
          </>
        ),
    },
  ];

  return (
    <div>
      <div style={styles.statRow}>
        {portalChecked ? (
          <StatTile
            label="Determination"
            value={result.determination?.display_label ?? result.determination?.label ?? network.text}
            tone={committedDeterminationTone(result.determination)}
            confidence={result.determination?.confidence}
          />
        ) : (
          <PendingTile label="Determination" />
        )}
        <StatTile
          label="Coverage"
          value={result.coverage_active == null ? 'N/A' : result.coverage_active ? 'ACTIVE' : 'INACTIVE'}
          tone={result.coverage_active == null ? 'neutral' : result.coverage_active ? 'success' : 'danger'}
        />
        {portalChecked ? (
          <StatTile
            label="Network Status"
            value={network.text}
            tone={networkTone}
            confidence={result.determination?.confidence}
          />
        ) : (
          <PendingTile label="Network Status" />
        )}
        <StatTile
          label="PCP Required"
          value={result.pcp_required == null ? 'N/A' : result.pcp_required ? 'YES' : 'NO'}
          tone={result.pcp_required ? 'warning' : 'neutral'}
        />
        <StatTile
          label="Prior Auth"
          value={result.prior_auth_required == null ? 'N/A' : result.prior_auth_required ? 'YES' : 'NO'}
          tone={result.prior_auth_required ? 'danger' : 'neutral'}
        />
        <StatTile
          label="Referral"
          value={result.referral_required == null ? 'N/A' : result.referral_required ? 'YES' : 'NO'}
          tone={result.referral_required ? 'warning' : 'neutral'}
        />
      </div>

      {(result.plan_name || result.group) && (
        <div style={styles.metaRow}>
          {result.plan_name && (
            <span>
              <strong>Plan:</strong> {result.plan_name}
            </span>
          )}
          {result.group && (
            <span>
              <strong>Group:</strong> {result.group}
            </span>
          )}
        </div>
      )}

      {result.network_verdict && portalChecked && (
        <div style={styles.verdictBanner}>
          <div style={styles.verdictTitle}>
            {verdictHeadline(result.network_verdict, network)}
          </div>
          {result.network_verdict.notes && <div style={styles.verdictBody}>{result.network_verdict.notes}</div>}
        </div>
      )}
      {/* Before the portal has spoken, say what is missing rather than showing a directory-only
          read that the walk may be about to overturn — flex.optum put Naar IN where UHC's own
          member-facing portal said OON, and the portal was right. */}
      {!portalChecked && (
        <div style={styles.pendingBanner}>
          <div style={styles.verdictTitle}>Network verdict pending the payer's portal</div>
          <div style={styles.verdictBody}>
            Coverage and benefits above are settled from the 271. The network determination is held
            back until the payer's own find-a-doctor portal has been checked — it is the
            member-facing directory, so it is the accuracy check on every other source. Open{' '}
            <strong>Portal proof</strong> below to run it.
          </div>
        </div>
      )}

      <Card style={{ marginBottom: 16 }} styles={{ body: { padding: 0 } }}>
        <Tabs items={tabItems} tabBarStyle={styles.tabBar} />
      </Card>

      <Divider />
      <div style={{ textAlign: 'center' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          Request ID: <strong>{result.request_id}</strong> · Audit recorded
        </Text>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  emptyState: {
    border: `1px dashed ${palette.slate300}`,
    borderRadius: 10,
    padding: '48px 24px',
    textAlign: 'center',
    background: '#fff',
  },
  statRow: { display: 'flex', gap: 12, marginBottom: 16 },
  statTile: { flex: 1, background: '#fff', border: `1px solid ${palette.slate200}`, borderRadius: 10, padding: '12px 14px' },
  statLabel: { fontSize: 10, fontWeight: 700, color: palette.slate400, letterSpacing: '0.4px', marginBottom: 6, textTransform: 'uppercase' },
  meterRow: { display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 },
  meter: { display: 'flex', gap: 3, flex: 'none' },
  meterSeg: { width: 14, height: 4, borderRadius: 999, display: 'inline-block' },
  meterText: { fontSize: 10, color: palette.slate400, textTransform: 'uppercase', letterSpacing: '0.3px' },
  pendingPill: {
    fontSize: 11,
    fontWeight: 600,
    padding: '3px 10px',
    borderRadius: 999,
    color: palette.slate500,
    background: palette.slate100,
    border: `1px dashed ${palette.slate300}`,
  },
  pendingNote: { fontSize: 10, color: palette.slate400, marginTop: 8, lineHeight: 1.5 },
  statPill: {
    fontSize: 11,
    fontWeight: 700,
    padding: '3px 10px',
    borderRadius: 999,
    // The longest label — "Out-of-Network with benefits" — wraps in a tile this width, and a wrapped
    // inline span otherwise renders its background as two ragged half-pills. `clone` gives each line
    // its own padding and radius so it reads as a pill either way.
    boxDecorationBreak: 'clone',
    WebkitBoxDecorationBreak: 'clone',
    display: 'inline',
  },
  metaRow: { display: 'flex', gap: 24, fontSize: 13, color: palette.slate700, marginBottom: 16 },
  verdictBanner: { background: palette.brand50, borderLeft: `3px solid ${palette.brand500}`, borderRadius: 8, padding: '12px 16px', marginBottom: 16 },
  pendingBanner: {
    background: palette.slate100,
    borderLeft: `3px solid ${palette.slate300}`,
    borderRadius: 8,
    padding: '12px 16px',
    marginBottom: 16,
  },
  verdictTitle: { color: palette.slate900, fontWeight: 600, fontSize: 13 },
  verdictBody: { color: palette.slate600, fontSize: 12, marginTop: 4 },
  tabBar: { padding: '0 18px', marginBottom: 0 },
  tabPad: { padding: '16px 18px' },
  tabLabel: { display: 'inline-flex', alignItems: 'center', gap: 6 },
  tabDot: { width: 6, height: 6, borderRadius: 999, display: 'inline-block' },
  legendRow: { padding: '10px 18px', borderBottom: `1px solid ${palette.slate100}`, display: 'flex', gap: 12 },
  legendItem: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: palette.slate600 },
  legendDot: { width: 7, height: 7, borderRadius: 999, display: 'inline-block' },
};
