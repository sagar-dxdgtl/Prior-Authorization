import { useState } from 'react';
import { Form, Input, Button, Typography } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { login, changePassword, logout } from '../services/auth';
import { palette, FONT_STACK } from '../theme/tokens';

const { Title, Text } = Typography;

interface LoginFormValues {
  username: string;
  password: string;
}

interface ChangePassFormValues {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

type ScreenMode = 'login' | 'change_password';

const FEATURES = [
  'In-network vs out-of-network cost-share matrix',
  'Deductible & OOP-max tracking',
  'Prior-auth and referral flags',
  'Full audit trail per request',
];

export default function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<ScreenMode>('login');
  const [loading, setLoading] = useState(false);
  const [loginForm] = Form.useForm<LoginFormValues>();
  const [changeForm] = Form.useForm<ChangePassFormValues>();

  const handleLogin = async (values: LoginFormValues) => {
    setLoading(true);
    try {
      const result = await login(values.username, values.password);
      if (result.must_change_password) {
        toast.info('Please set a new password before continuing.');
        changeForm.setFieldsValue({ current_password: values.password });
        setMode('change_password');
      } else {
        navigate('/');
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = async (values: ChangePassFormValues) => {
    setLoading(true);
    try {
      await changePassword(values.current_password, values.new_password, values.confirm_password);
      toast.success('Password updated. Please log in with your new password.');
      logout();
      setMode('login');
      loginForm.resetFields();
      changeForm.resetFields();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Password change failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.root}>
      <div style={styles.brand}>
        <div style={styles.brandInner}>
          <div style={styles.logo}>
            <span style={styles.logoMark}>P</span>
            <span style={styles.logoText}>PriorAuth</span>
          </div>
          <Title level={2} style={styles.brandHeadline}>
            Real-time eligibility &amp; benefits verification
          </Title>
          <Text style={styles.brandSub}>
            Instantly verify member coverage, understand cost-sharing, and streamline prior
            authorization — powered by live payer data.
          </Text>
          <div style={styles.featureList}>
            {FEATURES.map((f) => (
              <div key={f} style={styles.featureItem}>
                <span style={styles.featureCheck}>
                  <CheckOutlined style={{ fontSize: 9 }} />
                </span>
                <span>{f}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={styles.formPanel}>
        <div style={styles.formCard}>
          {mode === 'login' ? (
            <>
              <Title level={3} style={styles.formTitle}>
                Sign in
              </Title>
              <Text type="secondary" style={styles.formSub}>
                Enter your credentials to continue
              </Text>
              <Form
                form={loginForm}
                layout="vertical"
                onFinish={handleLogin}
                style={{ marginTop: 24 }}
                requiredMark={false}
              >
                <Form.Item
                  name="username"
                  label="Username"
                  rules={[{ required: true, message: 'Username is required' }]}
                >
                  <Input size="large" placeholder="admin" autoComplete="username" />
                </Form.Item>
                <Form.Item
                  name="password"
                  label="Password"
                  rules={[{ required: true, message: 'Password is required' }]}
                  style={{ marginBottom: 24 }}
                >
                  <Input.Password size="large" placeholder="••••••••" autoComplete="current-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit" size="large" block loading={loading}>
                  Sign in
                </Button>
              </Form>
            </>
          ) : (
            <>
              <Title level={3} style={styles.formTitle}>
                Set new password
              </Title>
              <Text type="secondary" style={styles.formSub}>
                Your account requires a password change before you can continue.
              </Text>
              <Form
                form={changeForm}
                layout="vertical"
                onFinish={handleChangePassword}
                style={{ marginTop: 24 }}
                requiredMark={false}
              >
                <Form.Item
                  name="current_password"
                  label="Current password"
                  rules={[{ required: true, message: 'Current password is required' }]}
                >
                  <Input.Password size="large" autoComplete="current-password" />
                </Form.Item>
                <Form.Item
                  name="new_password"
                  label="New password"
                  rules={[
                    { required: true, message: 'New password is required' },
                    { min: 8, message: 'Minimum 8 characters' },
                  ]}
                >
                  <Input.Password size="large" autoComplete="new-password" />
                </Form.Item>
                <Form.Item
                  name="confirm_password"
                  label="Confirm new password"
                  dependencies={['new_password']}
                  rules={[
                    { required: true, message: 'Please confirm your password' },
                    ({ getFieldValue }) => ({
                      validator(_, value) {
                        if (!value || getFieldValue('new_password') === value) {
                          return Promise.resolve();
                        }
                        return Promise.reject(new Error('Passwords do not match'));
                      },
                    }),
                  ]}
                  style={{ marginBottom: 24 }}
                >
                  <Input.Password size="large" autoComplete="new-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit" size="large" block loading={loading}>
                  Update password
                </Button>
                <Button
                  type="link"
                  block
                  style={{ marginTop: 8 }}
                  onClick={() => {
                    logout();
                    setMode('login');
                    changeForm.resetFields();
                  }}
                >
                  Back to sign in
                </Button>
              </Form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    minHeight: '100vh',
    fontFamily: FONT_STACK,
  },
  brand: {
    flex: '0 0 42%',
    background: palette.slate900,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '48px 56px',
  },
  brandInner: {
    maxWidth: 420,
    color: '#fff',
  },
  logo: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 40,
  },
  logoMark: {
    width: 28,
    height: 28,
    borderRadius: 7,
    background: palette.brand500,
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 700,
    fontSize: 14,
  },
  logoText: {
    fontSize: 16,
    fontWeight: 700,
    color: '#fff',
    letterSpacing: '-0.2px',
  },
  brandHeadline: {
    color: '#fff',
    marginBottom: 16,
    lineHeight: 1.35,
    fontSize: 22,
  },
  brandSub: {
    color: 'rgba(255,255,255,0.72)',
    fontSize: 13,
    lineHeight: 1.6,
    display: 'block',
    marginBottom: 32,
  },
  featureList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  featureItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    color: 'rgba(255,255,255,0.85)',
    fontSize: 12.5,
  },
  featureCheck: {
    width: 16,
    height: 16,
    borderRadius: 4,
    background: 'rgba(11,110,143,0.35)',
    color: '#5FD4C0',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  formPanel: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: palette.slate50,
    padding: '48px 24px',
  },
  formCard: {
    background: '#fff',
    borderRadius: 10,
    border: `1px solid ${palette.slate200}`,
    boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
    padding: '32px 40px',
    width: '100%',
    maxWidth: 380,
  },
  formTitle: {
    marginBottom: 4,
  },
  formSub: {
    fontSize: 13,
  },
};
