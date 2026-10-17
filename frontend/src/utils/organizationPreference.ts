/** Last selected scope is a preference, never proof of membership. */
const KEY = 'auth_organization_preferences';

type Preferences = Record<string, string | null>;
function read(): Preferences {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Preferences : {};
  } catch { return {}; }
}

export function rememberOrganization(userId: string, orgId: string | null) {
  const preferences = read();
  preferences[userId] = orgId;
  localStorage.setItem(KEY, JSON.stringify(preferences));
}

export function preferredOrganization(userId: string): string | null | undefined {
  const value = read()[userId];
  return value === null || typeof value === 'string' ? value : undefined;
}

export function rememberActiveOrganization() {
  try {
    const user = JSON.parse(localStorage.getItem('user') || 'null');
    if (typeof user?.id === 'string') {
      const orgId = localStorage.getItem('current_org_id');
      if (orgId) rememberOrganization(user.id, orgId);
    }
  } catch { /* Corrupt cached identity must not be assigned to another account. */ }
}
