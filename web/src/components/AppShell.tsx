import type { ReactNode } from 'react';
import { FileSearchOutlined } from '@ant-design/icons';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { getUser, logout } from '../services/auth';
import { palette, FONT_STACK } from '../theme/tokens';

interface NavItem {
  key: string;
  label: string;
  path: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'eligibility', label: 'Eligibility Check', path: '/', icon: <FileSearchOutlined /> },
];

interface AppShellProps {
  pageTitle: string;
  children: ReactNode;
}

export default function AppShell({ pageTitle, children }: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const user = getUser();

  const handleSignOut = () => {
    logout();
    navigate('/login');
  };

  return (
    <div style={styles.root}>
      <div style={styles.rail}>
        <div style={styles.brand}>
          <span style={styles.brandMark}>P</span>
          <span style={styles.brandName}>PriorAuth</span>
        </div>
        <nav style={styles.nav}>
          {NAV_ITEMS.map((item) => {
            const active = location.pathname === item.path;
            return (
              <Link
                key={item.key}
                to={item.path}
                style={{ ...styles.navItem, ...(active ? styles.navItemActive : {}) }}
              >
                <span style={styles.navIcon}>{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div style={styles.railFooter}>
          {user && (
            <div style={styles.userInfo}>
              <div style={styles.userName}>{user.username}</div>
              {user.role && <div style={styles.userRole}>{user.role}</div>}
            </div>
          )}
          <button style={styles.signOutBtn} onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </div>
      <div style={styles.main}>
        <div style={styles.topBar}>
          <span style={styles.pageTitle}>{pageTitle}</span>
        </div>
        <div style={styles.content}>{children}</div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: { display: 'flex', minHeight: '100vh', fontFamily: FONT_STACK },
  rail: {
    width: 200,
    flexShrink: 0,
    background: '#ffffff',
    borderRight: `1px solid ${palette.slate200}`,
    display: 'flex',
    flexDirection: 'column',
    padding: '16px 12px',
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '0 6px',
    marginBottom: 24,
  },
  brandMark: {
    width: 22,
    height: 22,
    borderRadius: 6,
    background: palette.brand500,
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 700,
    fontSize: 12,
    flexShrink: 0,
  },
  brandName: {
    fontWeight: 700,
    fontSize: 14,
    color: palette.slate900,
    letterSpacing: '-0.2px',
  },
  nav: { display: 'flex', flexDirection: 'column', gap: 4 },
  navItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 10px',
    borderRadius: 6,
    borderLeft: '3px solid transparent',
    color: palette.slate500,
    fontSize: 13,
    fontWeight: 600,
    textDecoration: 'none',
  },
  navItemActive: {
    background: palette.brand50,
    color: palette.brand500,
    borderLeft: `3px solid ${palette.brand500}`,
  },
  navIcon: { fontSize: 14, display: 'inline-flex' },
  railFooter: {
    marginTop: 'auto',
    borderTop: `1px solid ${palette.slate200}`,
    paddingTop: 12,
  },
  userInfo: { marginBottom: 8 },
  userName: { fontSize: 12, fontWeight: 600, color: palette.slate700 },
  userRole: { fontSize: 11, color: palette.slate400 },
  signOutBtn: {
    border: 'none',
    background: 'transparent',
    color: palette.slate500,
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    padding: 0,
  },
  main: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    background: palette.slate50,
    minWidth: 0,
  },
  topBar: {
    background: '#ffffff',
    borderBottom: `1px solid ${palette.slate200}`,
    padding: '12px 24px',
  },
  pageTitle: { fontSize: 14, fontWeight: 700, color: palette.slate900 },
  content: { padding: 24, flex: 1 },
};
