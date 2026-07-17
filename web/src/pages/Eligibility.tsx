import { useState, useRef } from 'react';
import { Form, Input, Button, Card, Typography, Select, Tag } from 'antd';
import { toast } from 'react-toastify';
import { apiFetch } from '../services/auth';
import { searchPayers, recheckNetwork, type PayerOption } from '../services/payers';
import AppShell from '../components/AppShell';
import { palette } from '../theme/tokens';
import ResultsView, { type EligibilityResponse, type NetworkVerdict } from '../components/ResultsView';

const { Text } = Typography;

interface EligibilityRequest {
  payer: string;
  npi: string;
  member_id: string;
  dob: string;
  first_name: string;
  last_name: string;
  plan?: string;
  state?: string;
  zip?: string;
  tin?: string;
  base_url?: string;
}

export default function Eligibility() {
  const [form] = Form.useForm<EligibilityRequest>();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<EligibilityResponse | null>(null);
  const [submitted, setSubmitted] = useState<EligibilityRequest | null>(null);
  const [formCollapsed, setFormCollapsed] = useState(false);
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
      searchPayers(q, form.getFieldValue('state'))
        .then((opts) => {
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
      const res = await apiFetch('/eligibility', { method: 'POST', body: JSON.stringify(payload) });
      const data = (await res.json()) as EligibilityResponse & { message?: string };
      if (!res.ok) {
        throw new Error(data.message ?? `Request failed (${res.status})`);
      }
      setSubmitted(values);
      setResult(data);
      setFormCollapsed(true); // shrink the form to give the results the space
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Eligibility check failed');
    } finally {
      setLoading(false);
    }
  };

  const collapsed = formCollapsed && !!result;

  return (
    <AppShell pageTitle="Eligibility Check">
      <Card style={{ marginBottom: 24 }} styles={{ body: { padding: 0 } }}>
        <div style={styles.cardHeader}>
          <span>Member Eligibility Check</span>
          {result && (
            <Button
              type="link"
              size="small"
              style={{ padding: 0, height: 'auto', fontSize: 12 }}
              onClick={() => setFormCollapsed((c) => !c)}
            >
              {collapsed ? 'Edit inputs' : 'Collapse'}
            </Button>
          )}
        </div>

        {collapsed && submitted && (
          <div style={styles.summaryBody}>
            <span style={styles.summaryStrong}>{selectedPayer?.label ?? submitted.payer}</span>
            {selectedPayer?.market ? <span style={styles.summaryDim}> · {selectedPayer.market}</span> : null}
            <span style={styles.summaryDim}> · </span>
            <span>
              {submitted.first_name} {submitted.last_name}
            </span>
            <span style={styles.summaryDim}> · NPI {submitted.npi}</span>
            {submitted.member_id ? <span style={styles.summaryDim}> · Member {submitted.member_id}</span> : null}
            {submitted.dob ? <span style={styles.summaryDim}> · DOB {submitted.dob}</span> : null}
          </div>
        )}

        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          requiredMark
          style={collapsed ? { display: 'none' } : undefined}
        >
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
                  onSelect={(value) => setSelectedPayer(payerOptions.find((o) => o.value === value) ?? null)}
                  notFoundContent={payerSearching ? 'Searching…' : 'Type at least 2 letters of a payer name'}
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
                <Form.Item name="npi" label="NPI" rules={[{ required: true, message: 'NPI is required' }]} style={{ flex: 1 }}>
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
              <Form.Item name="member_id" label="Member ID" rules={[{ required: true, message: 'Member ID is required' }]}>
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

      {result && result.plan_candidates?.length > 0 && (
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
                      selectedPayer?.source === 'stedi' ? selectedPayer.stedi_payer_id ?? undefined : undefined,
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
              Derived from the payer's 271. Change it to re-check the network for another of this member's coverages.
            </Text>
          </div>
        </Card>
      )}

      <ResultsView result={result} />
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
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  summaryBody: {
    padding: '14px 18px',
    fontSize: 13,
    color: palette.slate700,
  },
  summaryStrong: {
    fontWeight: 600,
    color: palette.slate900,
  },
  summaryDim: {
    color: palette.slate400,
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
  cardHeaderTitle: {
    fontWeight: 700,
    fontSize: 13,
    color: palette.slate900,
  },
};
