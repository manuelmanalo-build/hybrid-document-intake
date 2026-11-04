import { ClaimQueueItem, ClaimQueueView, ClaimWorkbench } from './claims-api.service';

export function queueItem(overrides: Partial<ClaimQueueItem> = {}): ClaimQueueItem {
  return {
    review_task_id: 'task-c10',
    document_id: 'doc-c10',
    task_status: 'OPEN',
    claimed_by_user_id: null,
    original_filename: 'C10_packet.pdf',
    policy_number: 'NFM-PA-4472433',
    named_insured: 'Danielle Okafor',
    vehicle: '2020 Toyota RAV4 XLE',
    loss_date: '2026-09-22',
    cause_of_loss: 'Rear-ended by other party',
    estimate_total: '2415.90',
    route: {
      review_queue: 'BODILY_INJURY_ADJUSTER',
      reason_code: 'INJURY_INDICATED',
      flags: ['INJURY_INDICATED', 'INJURY_CONFLICT', 'THIRD_PARTY_INVOLVED'],
      routed_by: 'RULES',
      routing_version: 'claim-routing-v1',
    },
    uploaded_at: '2026-10-22T08:45:00Z',
    age_minutes: 155,
    service_level: { target_minutes: 120, status: 'OVERDUE', minutes_remaining: -35 },
    ...overrides,
  };
}

export function queueView(items: readonly ClaimQueueItem[]): ClaimQueueView {
  const counts: Record<string, number> = {};
  for (const item of items) {
    counts[item.route.review_queue] = (counts[item.route.review_queue] ?? 0) + 1;
  }
  return {
    generated_at: '2026-10-22T11:20:00Z',
    queue: null,
    items,
    counts_by_queue: counts,
    counts_by_service_level: { OVERDUE: 1, DUE_SOON: 0, ON_TRACK: items.length - 1 },
  };
}

export function workbench(claimedBy: string | null = null): ClaimWorkbench {
  const field = (name: string, value: unknown, page: number) => ({
    name,
    value,
    confidence: '0.9100',
    source_page: page,
    evidence_excerpt: `Evidence for ${name}`,
    reviewed: false,
  });
  return {
    item: queueItem({ claimed_by_user_id: claimedBy }),
    review_cycle: 1,
    rules: [
      {
        rule_id: 'CR-001',
        name: 'Policy number resolves to a tenant policy',
        outcome: 'PASS',
        reason_code: null,
      },
      {
        rule_id: 'CR-009',
        name: 'No injury is indicated anywhere in the packet',
        outcome: 'REVIEW',
        reason_code: 'INJURY_INDICATED',
      },
    ],
    fields: [
      field('policy_number', 'NFM-PA-4472433', 1),
      field('injury_indicated_on_form', false, 1),
      field('injury_mentioned_in_correspondence', true, 3),
      field('vin_reported', '4T1L4E116LH848018', 1),
      field('vin_on_estimate', '4T1L4E116LH848018', 2),
      field('estimate_total', '2415.90', 2),
    ],
    segments: [
      { page_number: 1, label: 'LOSS_NOTICE', confidence: '0.95' },
      { page_number: 2, label: 'REPAIR_ESTIMATE', confidence: '0.95' },
      { page_number: 3, label: 'CORRESPONDENCE', confidence: '0.95' },
    ],
    reroutes: [],
  };
}
