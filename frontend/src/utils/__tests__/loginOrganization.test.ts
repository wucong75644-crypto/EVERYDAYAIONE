import { beforeEach, describe, expect, it, vi } from 'vitest';
const { listOrgs } = vi.hoisted(() => ({ listOrgs: vi.fn() }));
vi.mock('../../services/auth', () => ({ listMyOrganizations: listOrgs, getCurrentUser: vi.fn() }));
import { useAuthStore } from '../../stores/useAuthStore';
import { completeLogin, resolveLoginOrganization, stageLogin } from '../loginOrganization';
import { preferredOrganization } from '../organizationPreference';
import type { User, Organization, TokenInfo } from '../../types/auth';
const user = { id: 'user-a' } as User;
const otherUser = { id: 'user-b' } as User;
const org: Organization = { org_id: 'org-a', name: '企业A', role: 'member' };
const token = { access_token: 'access', refresh_token: 'refresh' } as TokenInfo;
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  useAuthStore.setState({ user: null, currentOrg: null, currentOrgId: null, isAuthenticated: false });
  listOrgs.mockResolvedValue([org]);
});
function existingEnterprise() { completeLogin(user, org); }

describe('login organization restoration', () => {
  it('same account re-login restores only the validated enterprise', async () => {
    existingEnterprise(); useAuthStore.getState().clearAuth();
    expect(localStorage.getItem('current_org_id')).toBeNull();
    expect(preferredOrganization(user.id)).toBe(org.org_id);
    stageLogin(token);
    const result = await resolveLoginOrganization(user.id);
    expect(result.needsChoice).toBe(false);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    completeLogin(user, result.organization);
    expect(localStorage.getItem('current_org_id')).toBe(org.org_id);
  });
  it('different accounts never inherit the prior enterprise', async () => {
    existingEnterprise(); stageLogin(token); listOrgs.mockResolvedValue([]);
    const result = await resolveLoginOrganization(otherUser.id);
    completeLogin(otherUser, result.organization);
    expect(useAuthStore.getState().currentOrgId).toBeNull();
    expect(preferredOrganization(user.id)).toBe(org.org_id);
  });
  it('removed membership requires a choice, not silent personal login', async () => {
    existingEnterprise(); stageLogin(token); listOrgs.mockResolvedValue([]);
    expect((await resolveLoginOrganization(user.id)).needsChoice).toBe(true);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
  it('temporary API failures preserve the preference without completing login', async () => {
    existingEnterprise(); stageLogin(token); listOrgs.mockRejectedValue(new Error('temporary failure'));
    await expect(resolveLoginOrganization(user.id)).rejects.toThrow('temporary failure');
    expect(preferredOrganization(user.id)).toBe(org.org_id);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
  it('an explicit personal choice stays personal', async () => {
    completeLogin(user, null); stageLogin(token);
    expect(await resolveLoginOrganization(user.id)).toEqual({ organization: null, choices: [], needsChoice: false });
    expect(listOrgs).not.toHaveBeenCalled();
  });
  it('enterprise links override preferences only after validating membership', async () => {
    existingEnterprise(); stageLogin(token); const nextOrg = { ...org, org_id: 'org-b' };
    listOrgs.mockResolvedValue([org, nextOrg]);
    expect((await resolveLoginOrganization(user.id, 'org-b')).organization).toEqual(nextOrg);
    expect((await resolveLoginOrganization(user.id, 'not-a-member')).needsChoice).toBe(true);
  });
  it('unknown prior scope offers accessible organizations instead of guessing', async () => {
    stageLogin(token);
    expect((await resolveLoginOrganization(user.id)).needsChoice).toBe(true);
  });
  it('direct account switching clears active org and cached conversations', () => {
    existingEnterprise(); localStorage.setItem('everydayai_conversations_cache', 'old');
    useAuthStore.getState().setUser(otherUser);
    expect(useAuthStore.getState().currentOrgId).toBeNull();
    expect(localStorage.getItem('everydayai_conversations_cache')).toBeNull();
    expect(preferredOrganization(user.id)).toBe(org.org_id);
  });
});
