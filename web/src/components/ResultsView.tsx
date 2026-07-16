import { Table, Card, Typography, Divider, Tabs } from 'antd';
import type { TableColumnsType } from 'antd';
import { palette } from '../theme/tokens';

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

function networkStatusTone(status: EligibilityResponse['network_status']): Tone {
  if (status === 'IN_NETWORK') return 'success';
  if (status === 'OUT_OF_NETWORK') return 'danger';
  if (status === 'REVIEW') return 'warning';
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

function StatTile({ label, value, tone }: { label: string; value: string; tone: Tone }) {
  const c = TONE_COLORS[tone];
  return (
    <div style={styles.statTile}>
      <div style={styles.statLabel}>{label}</div>
      <span style={{ ...styles.statPill, color: c.text, background: c.bg }}>{value}</span>
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

export default function ResultsView({ result }: { result: EligibilityResponse | null }) {
  if (!result) {
    return (
      <div style={styles.emptyState}>
        <Text type="secondary">Run a check to see coverage, network status, and cost-share details.</Text>
      </div>
    );
  }

  const tinSignal = result.corroboration?.find((s) => s.source === 'TIN-scope') ?? null;
  const tinTone: Tone = tinSignal ? tinScopeTone(tinSignal.result) : 'neutral';
  const networkTone = networkStatusTone(result.network_status);
  const costShareTone: Tone = result.benefits.length > 0 ? 'success' : 'neutral';

  const tabItems = [
    {
      key: 'network',
      label: <TabLabel tone={networkTone} text="Network Finding" />,
      children: (
        <div style={styles.tabPad}>
          {result.network_verdict ? (
            <>
              <div style={styles.verdictTitle}>
                {result.network_verdict.status.replace(/_/g, ' ')} · confidence: {result.network_verdict.confidence}
              </div>
              {result.network_verdict.notes && <div style={{ ...styles.verdictBody, marginTop: 6 }}>{result.network_verdict.notes}</div>}
              {result.network_verdict.source_url && (
                <div style={{ marginTop: 8 }}>
                  <a href={result.network_verdict.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                    View source
                  </a>
                </div>
              )}
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
      label: <TabLabel tone={tinTone} text="TIN-Scope" />,
      children: (
        <div style={styles.tabPad}>
          {tinSignal ? (
            <div>
              <span style={{ ...styles.statPill, color: TONE_COLORS[tinTone].text, background: TONE_COLORS[tinTone].bg }}>
                {tinSignal.result.toUpperCase()}
              </span>
              <div style={{ ...styles.verdictBody, marginTop: 6 }}>{tinSignal.detail}</div>
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              No billing TIN evaluated for this case.
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
        <StatTile
          label="Coverage"
          value={result.coverage_active == null ? 'N/A' : result.coverage_active ? 'ACTIVE' : 'INACTIVE'}
          tone={result.coverage_active == null ? 'neutral' : result.coverage_active ? 'success' : 'danger'}
        />
        <StatTile label="Network Status" value={result.network_status.replace(/_/g, ' ')} tone={networkTone} />
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

      {result.network_verdict && (
        <div style={styles.verdictBanner}>
          <div style={styles.verdictTitle}>
            {result.network_verdict.status.replace(/_/g, ' ')} · confidence: {result.network_verdict.confidence}
          </div>
          {result.network_verdict.notes && <div style={styles.verdictBody}>{result.network_verdict.notes}</div>}
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
  statPill: { fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 999 },
  metaRow: { display: 'flex', gap: 24, fontSize: 13, color: palette.slate700, marginBottom: 16 },
  verdictBanner: { background: palette.brand50, borderLeft: `3px solid ${palette.brand500}`, borderRadius: 8, padding: '12px 16px', marginBottom: 16 },
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
