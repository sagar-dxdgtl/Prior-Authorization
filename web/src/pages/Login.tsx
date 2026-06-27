import { useState } from 'react';
import { Form, Input, Button, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import { login, changePassword, logout } from '../services/auth';

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
        // Pre-fill current_password from the login form value
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
      {/* Left panel — brand */}
      <div style={styles.brand}>
        <div style={styles.brandInner}>
          <div style={styles.logo}>
            <span style={styles.logoIcon}>⚕</span>
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
            {[
              'In-network vs out-of-network cost-share matrix',
              'Deductible &amp; OOP-max tracking',
              'Prior-auth and referral flags',
              'Full audit trail per request',
            ].map((f) => (
              <div key={f} style={styles.featureItem}>
                <span style={styles.featureCheck}>✓</span>
                <span dangerouslySetInnerHTML={{ __html: f }} />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right panel — form */}
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
                <Button
                  type="primary"
                  htmlType="submit"
                  size="large"
                  block
                  loading={loading}
                  style={styles.submitBtn}
                >
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
                <Button
                  type="primary"
                  htmlType="submit"
                  size="large"
                  block
                  loading={loading}
                  style={styles.submitBtn}
                >
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
  },
  brand: {
    flex: '0 0 45%',
    background: 'linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%)',
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
    gap: 12,
    marginBottom: 40,
  },
  logoIcon: {
    fontSize: 32,
  },
  logoText: {
    fontSize: 22,
    fontWeight: 700,
    color: '#fff',
    letterSpacing: '-0.5px',
  },
  brandHeadline: {
    color: '#fff',
    marginBottom: 16,
    lineHeight: 1.3,
  },
  brandSub: {
    color: 'rgba(255,255,255,0.72)',
    fontSize: 15,
    lineHeight: 1.6,
    display: 'block',
    marginBottom: 40,
  },
  featureList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  featureItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    color: 'rgba(255,255,255,0.85)',
    fontSize: 14,
  },
  featureCheck: {
    color: '#52c41a',
    fontWeight: 700,
    marginTop: 1,
  },
  formPanel: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#f5f7fa',
    padding: '48px 24px',
  },
  formCard: {
    background: '#fff',
    borderRadius: 16,
    padding: '40px 48px',
    width: '100%',
    maxWidth: 420,
    boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
  },
  formTitle: {
    marginBottom: 4,
  },
  formSub: {
    fontSize: 14,
  },
  submitBtn: {
    background: '#2c5364',
    borderColor: '#2c5364',
  },
};
