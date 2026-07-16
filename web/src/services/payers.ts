import { apiFetch } from './auth';

export interface PayerOption {
  value: string;
  label: string;
  market: string | null;
  benefit_type: string | null;
  stedi_payer_id: string | null;
  enrollment_status: string | null;
  source: 'roster' | 'stedi';
}

export async function searchPayers(q: string): Promise<PayerOption[]> {
  const res = await apiFetch(`/payers/search?q=${encodeURIComponent(q)}`);
  if (!res.ok) return [];
  return (await res.json()) as PayerOption[];
}

export interface RecheckResult {
  network_status: string;
  network_verdict: Record<string, unknown> | null;
  corroboration: { source: string; result: string; detail: string }[] | null;
}

export async function recheckNetwork(body: {
  payer: string;
  stedi_payer_id?: string;
  npi?: string;
  plan: string;
  state?: string;
  zip?: string;
  tin?: string;
  stedi_network_status: string;
}): Promise<RecheckResult> {
  const res = await apiFetch('/eligibility/recheck-network', { method: 'POST', body: JSON.stringify(body) });
  if (!res.ok) throw new Error('Re-check failed');
  return (await res.json()) as RecheckResult;
}
