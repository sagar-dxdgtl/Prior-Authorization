import { useState, useRef } from 'react';
import { Form, Input, Button, Table, Card, Typography, Divider, Select, Tag } from 'antd';
import type { TableColumnsType } from 'antd';
import { toast } from 'react-toastify';
import { apiFetch } from '../services/auth';
import { searchPayers, recheckNetwork, type PayerOption } from '../services/payers';
import AppShell from '../components/AppShell';
import { palette } from '../theme/tokens';

const { Text } = Typography;

interface EligibilityRequest {
  payer: string;
  npi: string;
  member_id: string;
  dob: string;
  first_name: string;
  last_name: string;
  plan?: string;
  state: string;
  zip: string;
  tin?: string;
  base_url?: string;
}

interface Benefit {
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

interface CorroborationSignal {
  source: string;
  result: string;
  detail: string;
}

interface NetworkVerdict {
  status: string;
  matched_provider: Record<string, unknown> | null;
  plan_or_network_checked: string;
  source_url: string;
  confidence: string;
  notes: string;
  corroboration: CorroborationSignal[] | null;
  evidence: Record<string, unknown> | null;
}

interface EligibilityResponse {
  request_id: string;
  coverage_active: boolean;
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
  plan_candidates: { plan: string; is_product: boolean; rank: number }[];
  selected_plan: string | null;
  stedi_network_status: string | null;
}

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

function networkStatusTone(status: EligibilityResponse['network_status']): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'IN_NETWORK') return 'success';
  if (status === 'OUT_OF_NETWORK') return 'danger';
  if (status === 'REVIEW') return 'warning';
  return 'neutral';
}

function tinScopeTone(result: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (result === 'corroborates') return 'success';
  if (result === 'contradicts') return 'danger';
  return 'neutral';
}

const TONE_COLORS: Record<string, { text: string; bg: string }> = {
  success: { text: palette.success, bg: palette.successBg },
  warning: { text: palette.warning, bg: palette.warningBg },
  danger: { text: palette.danger, bg: palette.dangerBg },
  neutral: { text: palette.slate500, bg: palette.slate100 },
};

function StatTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'success' | 'warning' | 'danger' | 'neutral';
}) {
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
      {met != null && (
        <div style={{ fontSize: 11, color: palette.slate400, marginTop: 2 }}>
          Met: ${met.toLocaleString()}
        </div>
      )}
      {remaining != null && (
        <div style={{ fontSize: 11, color: palette.slate400 }}>
          Rem: ${remaining.toLocaleString()}
        </div>
      )}
    </div>
  );
}

const matrixColumns: TableColumnsType<MatrixRow> = [
  {
    title: 'Service Type',
    dataIndex: 'service_type_label',
    key: 'service_type_label',
    width: 200,
    ellipsis: true,
  },
  {
    title: 'Category',
    dataIndex: 'category',
    key: 'category',
    width: 120,
    render: (v: string) => v.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
  },
  {
    title: 'Level',
    dataIndex: 'level',
    key: 'level',
    width: 100,
  },
  {
    title: 'Period',
    dataIndex: 'time_period',
    key: 'time_period',
    width: 100,
    render: (v: string | null) => v ?? '-',
  },
  {
    title: 'In-Network',
    key: 'in',
    width: 150,
    render: (_: unknown, row: MatrixRow) => (
      <CostCell value={row.in_value} met={row.in_met} remaining={row.in_remaining} />
    ),
  },
  {
    title: 'Out-of-Network',
    key: 'oon',
    width: 150,
    render: (_: unknown, row: MatrixRow) => (
      <CostCell value={row.oon_value} met={row.oon_met} remaining={row.oon_remaining} />
    ),
  },
];

export default function Eligibility() {
  const [form] = Form.useForm<EligibilityRequest>();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<EligibilityResponse | null>(null);
  const [payerOptions, setPayerOptions] = useState<PayerOption[]>([]);
  const [selectedPayer, setSelectedPayer] = useState<PayerOption | null>(null);
  const [payerSearching, setPayerSearching] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchSeq = useRef(0);

  const onPayerSearch = (q: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (q.trim().length < 2) {
      setPayerOptions([]);
      setPayerSearching(false);
      return;
    }
    setPayerSearching(true);
    const seq = ++searchSeq.current;
    searchTimer.current = setTimeout(() => {
      searchPayers(q)
        .then((opts) => {
          // ignore stale responses that resolve out of order
          if (seq === searchSeq.current) setPayerOptions(opts);
        })
        .catch(() => {
          if (seq === searchSeq.current) setPayerOptions([]);
        })
        .finally(() => {
          if (seq === searchSeq.current) setPayerSearching(false);
        });
    }, 250);
  };

  const handleSubmit = async (values: EligibilityRequest) => {
    setLoading(true);
    setResult(null);
    const payload = {
      ...values,
      stedi_payer_id:
        selectedPayer?.source === 'stedi' ? selectedPayer.stedi_payer_id ?? undefined : undefined,
    };
    try {
      const res = await apiFetch('/eligibility', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const data = (await res.json()) as EligibilityResponse & { message?: string };
      if (!res.ok) {
        throw new Error(data.message ?? `Request failed (${res.status})`);
      }
      setResult(data);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Eligibility check failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppShell pageTitle="Eligibility Check">
      <Card style={{ marginBottom: 24 }} styles={{ body: { padding: 0 } }}>
        <div style={styles.cardHeader}>Member Eligibility Check</div>
        <Form form={form} layout="vertical" onFinish={handleSubmit} requiredMark>
          <div style={styles.formGrid}>
            <div style={styles.formColFirst}>
              <div style={styles.sectionLabel}>Provider</div>
              <Form.Item name="payer" label="Payer" rules={[{ required: true, message: 'Payer is required' }]}>
                <Select
                  showSearch
                  filterOption={false}
                  loading={payerSearching}
                  placeholder="Search payers (e.g. Aetna, UnitedHealthcare)"
                  onSearch={onPayerSearch}
                  onSelect={(value) =>
                    setSelectedPayer(payerOptions.find((o) => o.value === value) ?? null)
                  }
                  notFoundContent={
                    payerSearching ? 'Searching…' : 'Type at least 2 letters of a payer name'
                  }
                  options={payerOptions.map((o) => ({
                    value: o.value,
                    label: (
                      <span>
                        {o.label}
                        {o.market ? <span style={{ color: palette.slate400 }}> · {o.market}</span> : null}
                        {o.enrollment_status === 'supported' ? (
                          <Tag color="green" style={{ marginLeft: 6 }}>
                            supported
                          </Tag>
                        ) : o.enrollment_status === 'needs_enrollment' ? (
                          <Tag color="gold" style={{ marginLeft: 6 }}>
                            needs enrollment
                          </Tag>
                        ) : o.source === 'stedi' ? (
                          <Tag style={{ marginLeft: 6 }}>Stedi</Tag>
                        ) : null}
                      </span>
                    ),
                  }))}
                />
              </Form.Item>
              <div style={{ display: 'flex', gap: 12 }}>
                <Form.Item
                  name="npi"
                  label="NPI"
                  rules={[{ required: true, message: 'NPI is required' }]}
                  style={{ flex: 1 }}
                >
                  <Input placeholder="10-digit NPI" />
                </Form.Item>
                <Form.Item name="tin" label="Billing TIN (optional)" style={{ flex: 1 }}>
                  <Input placeholder="e.g. 463812940" />
                </Form.Item>
              </div>
              <Form.Item name="base_url" label="Payer Base URL (optional)">
                <Input placeholder="https://..." />
              </Form.Item>
            </div>
            <div style={styles.formCol}>
              <div style={styles.sectionLabel}>Member</div>
              <Form.Item
                name="member_id"
                label="Member ID"
                rules={[{ required: true, message: 'Member ID is required' }]}
              >
                <Input placeholder="Member/subscriber ID" />
              </Form.Item>
              <div style={{ display: 'flex', gap: 12 }}>
                <Form.Item
                  name="first_name"
                  label="First Name"
                  rules={[{ required: true, message: 'First name is required' }]}
                  style={{ flex: 1 }}
                >
                  <Input placeholder="Member first name" />
                </Form.Item>
                <Form.Item
                  name="last_name"
                  label="Last Name"
                  rules={[{ required: true, message: 'Last name is required' }]}
                  style={{ flex: 1 }}
                >
                  <Input placeholder="Member last name" />
                </Form.Item>
              </div>
              <Form.Item name="dob" label="Date of Birth" rules={[{ required: true, message: 'DOB is required' }]}>
                <Input placeholder="MM/DD/YYYY" maxLength={10} />
              </Form.Item>
            </div>
            <div style={styles.formCol}>
              <div style={styles.sectionLabel}>Location</div>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
                Plan is read from the payer's 271 — no need to enter it.
              </Text>
              <div style={{ display: 'flex', gap: 12 }}>
                <Form.Item name="state" label="State (optional)" style={{ flex: 1 }}>
                  <Input placeholder="TX" maxLength={2} />
                </Form.Item>
                <Form.Item name="zip" label="ZIP (optional)" style={{ flex: 1 }}>
                  <Input placeholder="78701" maxLength={10} />
                </Form.Item>
              </div>
            </div>
          </div>
          <div style={styles.formFooter}>
            <Button type="primary" htmlType="submit" loading={loading} size="large">
              Check Eligibility
            </Button>
          </div>
        </Form>
      </Card>

      {!result && (
        <div style={styles.emptyState}>
          <Text type="secondary">Run a check to see coverage, network status, and cost-share details.</Text>
        </div>
      )}

      {result && (
        <div>
          <div style={styles.statRow}>
            <StatTile
              label="Coverage"
              value={result.coverage_active ? 'ACTIVE' : 'INACTIVE'}
              tone={result.coverage_active ? 'success' : 'danger'}
            />
            <StatTile
              label="Network Status"
              value={result.network_status.replace(/_/g, ' ')}
              tone={networkStatusTone(result.network_status)}
            />
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
                {result.network_verdict.status.replace(/_/g, ' ')} · confidence:{' '}
                {result.network_verdict.confidence}
              </div>
              {result.network_verdict.notes && (
                <div style={styles.verdictBody}>{result.network_verdict.notes}</div>
              )}
            </div>
          )}

          {(() => {
            const tinSignal = result.corroboration?.find((s) => s.source === 'TIN-scope') ?? null;
            const tone = tinSignal ? tinScopeTone(tinSignal.result) : 'neutral';
            const c = TONE_COLORS[tone];
            return (
              <Card style={{ marginBottom: 16 }} styles={{ body: { padding: '14px 18px' } }}>
                <div style={styles.cardHeaderTitle}>TIN-Scope Check (Group Billing)</div>
                {tinSignal ? (
                  <div style={{ marginTop: 8 }}>
                    <span style={{ ...styles.statPill, color: c.text, background: c.bg }}>
                      {tinSignal.result.toUpperCase()}
                    </span>
                    <div style={{ ...styles.verdictBody, marginTop: 6 }}>{tinSignal.detail}</div>
                  </div>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    No billing TIN evaluated for this case.
                  </Text>
                )}
              </Card>
            );
          })()}

          {result.plan_candidates?.length > 0 && (
            <Card style={{ marginBottom: 16 }} styles={{ body: { padding: '14px 18px' } }}>
              <div style={styles.cardHeaderTitle}>Plan used for network check</div>
              <div style={{ marginTop: 8, maxWidth: 460 }}>
                <Select
                  style={{ width: '100%' }}
                  value={result.selected_plan ?? undefined}
                  loading={rechecking}
                  options={result.plan_candidates.map((c) => ({
                    value: c.plan,
                    label: c.is_product ? c.plan : `${c.plan} (segment)`,
                  }))}
                  onChange={async (plan) => {
                    setRechecking(true);
                    try {
                      const upd = await recheckNetwork({
                        payer: form.getFieldValue('payer'),
                        stedi_payer_id:
                          selectedPayer?.source === 'stedi'
                            ? selectedPayer.stedi_payer_id ?? undefined
                            : undefined,
                        npi: form.getFieldValue('npi'),
                        plan,
                        state: form.getFieldValue('state'),
                        zip: form.getFieldValue('zip'),
                        tin: form.getFieldValue('tin'),
                        stedi_network_status: result.stedi_network_status ?? 'UNKNOWN',
                      });
                      setResult({
                        ...result,
                        selected_plan: plan,
                        network_status: upd.network_status as EligibilityResponse['network_status'],
                        network_verdict: upd.network_verdict as unknown as NetworkVerdict | null,
                        corroboration: upd.corroboration,
                      });
                    } catch {
                      toast.error('Network re-check failed');
                    } finally {
                      setRechecking(false);
                    }
                  }}
                />
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
                  Derived from the payer's 271. Change it to re-check the network for another of this
                  member's coverages.
                </Text>
              </div>
            </Card>
          )}

          <Card style={{ marginBottom: 16 }} styles={{ body: { padding: 0 } }}>
            <div style={styles.matrixHeader}>
              <span style={styles.cardHeaderTitle}>Cost-Share Matrix</span>
              <div style={styles.legend}>
                <span style={styles.legendItem}>
                  <span style={{ ...styles.legendDot, background: palette.success }} />
                  In-Network
                </span>
                <span style={styles.legendItem}>
                  <span style={{ ...styles.legendDot, background: palette.danger }} />
                  Out-of-Network
                </span>
              </div>
            </div>
            {result.benefits.length === 0 ? (
              <div style={{ padding: '16px 18px' }}>
                <Text type="secondary">No benefit details returned for this member.</Text>
              </div>
            ) : (
              <Table<MatrixRow>
                columns={matrixColumns}
                dataSource={buildMatrix(result.benefits)}
                pagination={{ pageSize: 20, showSizeChanger: false }}
                size="small"
                scroll={{ x: 820 }}
                onRow={(_, index) => ({
                  style: index != null && index % 2 === 1 ? { background: '#FAFBFC' } : {},
                })}
              />
            )}
          </Card>

          <Divider />
          <div style={{ textAlign: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Request ID: <strong>{result.request_id}</strong> · Audit recorded
            </Text>
          </div>
        </div>
      )}
    </AppShell>
  );
}

const styles: Record<string, React.CSSProperties> = {
  cardHeader: {
    padding: '14px 18px',
    borderBottom: `1px solid ${palette.slate100}`,
    fontWeight: 700,
    fontSize: 13,
    color: palette.slate900,
  },
  formGrid: {
    display: 'flex',
    alignItems: 'stretch',
    padding: '20px 20px',
  },
  formColFirst: {
    flex: 1,
    minWidth: 0,
    paddingRight: 28,
  },
  formCol: {
    flex: 1,
    minWidth: 0,
    paddingLeft: 28,
    paddingRight: 28,
    borderLeft: `1px solid ${palette.slate200}`,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: 700,
    color: palette.slate500,
    letterSpacing: '0.6px',
    paddingBottom: 10,
    marginBottom: 18,
    textTransform: 'uppercase',
    borderBottom: `1px solid ${palette.slate100}`,
  },
  formFooter: {
    padding: '12px 18px',
    borderTop: `1px solid ${palette.slate100}`,
    display: 'flex',
    justifyContent: 'flex-end',
  },
  emptyState: {
    border: `1px dashed ${palette.slate300}`,
    borderRadius: 10,
    padding: '48px 24px',
    textAlign: 'center',
    background: '#fff',
  },
  statRow: {
    display: 'flex',
    gap: 12,
    marginBottom: 16,
  },
  statTile: {
    flex: 1,
    background: '#fff',
    border: `1px solid ${palette.slate200}`,
    borderRadius: 10,
    padding: '12px 14px',
  },
  statLabel: {
    fontSize: 10,
    fontWeight: 700,
    color: palette.slate400,
    letterSpacing: '0.4px',
    marginBottom: 6,
    textTransform: 'uppercase',
  },
  statPill: {
    fontSize: 11,
    fontWeight: 700,
    padding: '3px 10px',
    borderRadius: 999,
  },
  metaRow: {
    display: 'flex',
    gap: 24,
    fontSize: 13,
    color: palette.slate700,
    marginBottom: 16,
  },
  verdictBanner: {
    background: palette.brand50,
    borderLeft: `3px solid ${palette.brand500}`,
    borderRadius: 8,
    padding: '12px 16px',
    marginBottom: 16,
  },
  verdictTitle: {
    color: palette.slate900,
    fontWeight: 600,
    fontSize: 13,
  },
  verdictBody: {
    color: palette.slate600,
    fontSize: 12,
    marginTop: 4,
  },
  matrixHeader: {
    padding: '14px 18px',
    borderBottom: `1px solid ${palette.slate100}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardHeaderTitle: {
    fontWeight: 700,
    fontSize: 13,
    color: palette.slate900,
  },
  legend: {
    display: 'flex',
    gap: 12,
  },
  legendItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 11,
    color: palette.slate600,
  },
  legendDot: {
    width: 7,
    height: 7,
    borderRadius: 999,
    display: 'inline-block',
  },
};
