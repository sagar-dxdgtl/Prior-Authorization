import { useState } from 'react';
import {
  Form,
  Input,
  Button,
  Table,
  Tag,
  Space,
  Typography,
  Divider,
  Row,
  Col,
  Card,
  Alert,
} from 'antd';
import type { TableColumnsType } from 'antd';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { apiFetch, logout, getUser } from '../services/auth';

const { Title, Text } = Typography;

interface EligibilityRequest {
  payer: string;
  npi: string;
  member_id: string;
  dob: string;
  first_name: string;
  last_name: string;
  plan: string;
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

interface EligibilityResponse {
  request_id: string;
  coverage_active: boolean;
  plan_name: string | null;
  group: string | null;
  network_status: 'IN' | 'OON' | 'REVIEW' | 'UNKNOWN';
  benefits: Benefit[];
  pcp_required: boolean | null;
  prior_auth_required: boolean | null;
  referral_required: boolean | null;
  cob: boolean | null;
  network_verdict: string | null;
  corroboration: string | null;
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

function networkStatusColor(status: string): string {
  if (status === 'IN') return 'green';
  if (status === 'OON') return 'red';
  if (status === 'REVIEW') return 'orange';
  return 'default';
}

function CostCell({ value, met, remaining }: { value: string; met: number | null; remaining: number | null }) {
  return (
    <div>
      <div style={{ fontWeight: 600 }}>{value}</div>
      {met != null && (
        <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>
          Met: ${met.toLocaleString()}
        </div>
      )}
      {remaining != null && (
        <div style={{ fontSize: 11, color: '#888' }}>
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
  const navigate = useNavigate();
  const user = getUser();
  const [form] = Form.useForm<EligibilityRequest>();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<EligibilityResponse | null>(null);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const handleSubmit = async (values: EligibilityRequest) => {
    setLoading(true);
    setResult(null);
    try {
      const res = await apiFetch('/eligibility', {
        method: 'POST',
        body: JSON.stringify(values),
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
    <div style={{ minHeight: '100vh', background: '#f5f7fa' }}>
      {/* Top nav */}
      <div style={navStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 20 }}>⚕</span>
          <Title level={4} style={{ margin: 0, color: '#fff' }}>
            PriorAuth
          </Title>
          <Tag color="blue" style={{ marginLeft: 8 }}>
            Eligibility
          </Tag>
        </div>
        <Space>
          {user && (
            <Text style={{ color: 'rgba(255,255,255,0.85)', fontSize: 13 }}>
              {user.username}
              {user.role ? ` · ${user.role}` : ''}
            </Text>
          )}
          <Button size="small" onClick={handleLogout} style={{ color: '#fff', borderColor: 'rgba(255,255,255,0.4)' }}>
            Sign out
          </Button>
        </Space>
      </div>

      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '32px 24px' }}>
        {/* Request form */}
        <Card
          title={<Title level={5} style={{ margin: 0 }}>Member Eligibility Check</Title>}
          style={{ marginBottom: 24, borderRadius: 12 }}
        >
          <Form
            form={form}
            layout="vertical"
            onFinish={handleSubmit}
            requiredMark={false}
          >
            <Row gutter={[16, 0]}>
              <Col span={8}>
                <Form.Item
                  name="payer"
                  label="Payer ID"
                  rules={[{ required: true, message: 'Payer is required' }]}
                >
                  <Input placeholder="e.g. BCBSTX" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="npi"
                  label="NPI"
                  rules={[{ required: true, message: 'NPI is required' }]}
                >
                  <Input placeholder="10-digit NPI" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="member_id"
                  label="Member ID"
                  rules={[{ required: true, message: 'Member ID is required' }]}
                >
                  <Input placeholder="Member/subscriber ID" />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={[16, 0]}>
              <Col span={8}>
                <Form.Item
                  name="first_name"
                  label="First Name"
                  rules={[{ required: true, message: 'First name is required' }]}
                >
                  <Input placeholder="Member first name" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="last_name"
                  label="Last Name"
                  rules={[{ required: true, message: 'Last name is required' }]}
                >
                  <Input placeholder="Member last name" />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="dob"
                  label="Date of Birth"
                  rules={[{ required: true, message: 'DOB is required' }]}
                >
                  <Input placeholder="YYYYMMDD" maxLength={8} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={[16, 0]}>
              <Col span={8}>
                <Form.Item
                  name="plan"
                  label="Plan"
                  rules={[{ required: true, message: 'Plan is required' }]}
                >
                  <Input placeholder="Plan name or code" />
                </Form.Item>
              </Col>
              <Col span={4}>
                <Form.Item
                  name="state"
                  label="State"
                  rules={[{ required: true, message: 'State is required' }]}
                >
                  <Input placeholder="TX" maxLength={2} />
                </Form.Item>
              </Col>
              <Col span={4}>
                <Form.Item
                  name="zip"
                  label="ZIP"
                  rules={[{ required: true, message: 'ZIP is required' }]}
                >
                  <Input placeholder="78701" maxLength={10} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item name="base_url" label="Payer Base URL (optional)">
                  <Input placeholder="https://..." />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                size="large"
                style={{ background: '#2c5364', borderColor: '#2c5364' }}
              >
                Check Eligibility
              </Button>
            </Form.Item>
          </Form>
        </Card>

        {/* Results */}
        {result && (
          <div>
            {/* Status badges */}
            <Card style={{ marginBottom: 16, borderRadius: 12 }}>
              <Row gutter={[24, 12]} align="middle">
                <Col>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                    Coverage
                  </Text>
                  <Tag
                    color={result.coverage_active ? 'green' : 'red'}
                    style={{ fontSize: 13, padding: '4px 10px' }}
                  >
                    {result.coverage_active ? 'ACTIVE' : 'INACTIVE'}
                  </Tag>
                </Col>
                <Col>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                    Network Status
                  </Text>
                  <Tag
                    color={networkStatusColor(result.network_status)}
                    style={{ fontSize: 13, padding: '4px 10px' }}
                  >
                    {result.network_status}
                  </Tag>
                </Col>
                {result.plan_name && (
                  <Col>
                    <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                      Plan
                    </Text>
                    <Text strong>{result.plan_name}</Text>
                  </Col>
                )}
                {result.group && (
                  <Col>
                    <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                      Group
                    </Text>
                    <Text>{result.group}</Text>
                  </Col>
                )}
                <Col>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                    PCP Required
                  </Text>
                  <Tag color={result.pcp_required ? 'orange' : 'default'}>
                    {result.pcp_required == null ? 'N/A' : result.pcp_required ? 'Yes' : 'No'}
                  </Tag>
                </Col>
                <Col>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                    Prior Auth
                  </Text>
                  <Tag color={result.prior_auth_required ? 'red' : 'default'}>
                    {result.prior_auth_required == null ? 'N/A' : result.prior_auth_required ? 'Yes' : 'No'}
                  </Tag>
                </Col>
                <Col>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                    Referral
                  </Text>
                  <Tag color={result.referral_required ? 'orange' : 'default'}>
                    {result.referral_required == null ? 'N/A' : result.referral_required ? 'Yes' : 'No'}
                  </Tag>
                </Col>
              </Row>
            </Card>

            {/* Network verdict / corroboration */}
            {result.network_verdict && (
              <Alert
                type="info"
                message={result.network_verdict}
                description={result.corroboration ?? undefined}
                showIcon
                style={{ marginBottom: 16, borderRadius: 8 }}
              />
            )}

            {/* Cost-share matrix */}
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Title level={5} style={{ margin: 0 }}>Cost-Share Matrix</Title>
                  <Space>
                    <Tag color="green">IN = In-Network</Tag>
                    <Tag color="red">OON = Out-of-Network</Tag>
                  </Space>
                </div>
              }
              style={{ borderRadius: 12 }}
            >
              {result.benefits.length === 0 ? (
                <Text type="secondary">No benefit details returned for this member.</Text>
              ) : (
                <Table<MatrixRow>
                  columns={matrixColumns}
                  dataSource={buildMatrix(result.benefits)}
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                  size="small"
                  scroll={{ x: 820 }}
                  bordered
                />
              )}
            </Card>

            {/* Audit note */}
            <Divider />
            <div style={{ textAlign: 'center' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Request ID: <strong>{result.request_id}</strong> · Audit recorded
              </Text>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const navStyle: React.CSSProperties = {
  background: 'linear-gradient(90deg, #0f2027 0%, #2c5364 100%)',
  padding: '12px 32px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
};
