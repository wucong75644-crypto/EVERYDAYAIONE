import { listMyOrganizations } from '../services/auth';
import { useAuthStore } from '../stores/useAuthStore';
import type { Organization, TokenInfo, User } from '../types/auth';
import { preferredOrganization } from './organizationPreference';

export interface OrganizationResolution {
  organization: Organization | null;
  choices: Organization[];
  needsChoice: boolean;
}

/** Stage credentials without marking login complete until scope has been resolved. */
export function stageLogin(token: TokenInfo) {
  const store = useAuthStore.getState();
  store.clearAuth();
  store.setTokens(token.access_token, token.refresh_token);
}

export async function resolveLoginOrganization(userId: string, explicitOrgId?: string): Promise<OrganizationResolution> {
  const preferred = explicitOrgId || preferredOrganization(userId);
  if (preferred === null) return { organization: null, choices: [], needsChoice: false };
  const choices = await listMyOrganizations();
  const organization = choices.find(org => org.org_id === preferred) || null;
  return { organization, choices, needsChoice: !organization && (Boolean(preferred) || choices.length > 0) };
}

export function completeLogin(user: User, organization: Organization | null) {
  const store = useAuthStore.getState();
  store.setUser(user);
  store.setCurrentOrg(organization);
}
