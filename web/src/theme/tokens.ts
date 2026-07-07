import type { ThemeConfig } from 'antd';

export const palette = {
  slate50: '#F8FAFC',
  slate100: '#F1F5F9',
  slate200: '#E2E8F0',
  slate300: '#CBD5E1',
  slate400: '#94A3B8',
  slate500: '#64748B',
  slate600: '#475569',
  slate700: '#334155',
  slate900: '#0F172A',

  brand500: '#0B6E8F',
  brand600: '#095A76',
  brand50: '#E6F3F7',

  success: '#12805C',
  successBg: '#E7F6EF',
  warning: '#B45309',
  warningBg: '#FEF3E2',
  danger: '#C0152F',
  dangerBg: '#FDECEC',
} as const;

export const FONT_STACK =
  "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.brand500,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.danger,
    colorBgLayout: palette.slate50,
    colorBorder: palette.slate200,
    colorText: palette.slate700,
    colorTextSecondary: palette.slate500,
    borderRadius: 6,
    fontFamily: FONT_STACK,
  },
  components: {
    Card: {
      borderRadiusLG: 10,
      colorBorderSecondary: palette.slate200,
    },
    Table: {
      headerBg: palette.slate50,
      headerColor: palette.slate400,
      borderColor: palette.slate200,
    },
  },
};
