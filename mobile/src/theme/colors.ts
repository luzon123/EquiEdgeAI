/** Minimal, single shared palette — one look across iOS and Android. */
export const colors = {
  background: '#0B0F14',
  surface: '#131A22',
  text: '#F5F7FA',
  textMuted: '#8B97A6',
  accent: '#3DD68C', // positive / continue actions (CALL, CHECK, BET, RAISE, ALL-IN)
  danger: '#E5484D', // FOLD
  border: '#232D38',
};

export const actionColor = (action: string): string =>
  action === 'FOLD' ? colors.danger : colors.accent;
