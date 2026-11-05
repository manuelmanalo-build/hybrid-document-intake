import { ReviewQueueItem, ReviewQueueView, Workbench } from './review-api.service';

export function queueItem(overrides: Partial<ReviewQueueItem> = {}): ReviewQueueItem {
  return {
    review_task_id: 'task-c10',
    document_id: 'doc-c10',
    document_type: 'CLAIM_PACKET',
    task_status: 'OPEN',
    claimed_by_user_id: null,
    original_filename: 'C10_packet.pdf',
    summary: {
      reference: 'NFM-PA-4472433',
      party: 'Danielle Okafor',
      subject: '2020 Toyota RAV4 XLE',
      event_label: 'Loss',
      event_date: '2026-09-22',
      context: 'Rear-ended by other party',
      amount: '2415.90',
      item_count: null,
    },
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

export function queueView(items: readonly ReviewQueueItem[]): ReviewQueueView {
  const counts: Record<string, number> = {};
  for (const item of items) {
    counts[item.route.review_queue] = (counts[item.route.review_queue] ?? 0) + 1;
  }
  return {
    document_type: items[0]?.document_type ?? 'CLAIM_PACKET',
    generated_at: '2026-10-22T11:20:00Z',
    queue: null,
    items,
    counts_by_queue: counts,
    counts_by_service_level: { OVERDUE: 1, DUE_SOON: 0, ON_TRACK: items.length - 1 },
  };
}

export function workbench(claimedBy: string | null = null): Workbench {
  const field = (name: string, value: unknown, page: number) => ({
    name,
    value,
    confidence: '0.9100',
    source_page: page,
    evidence_excerpt: `Evidence for ${name}`,
    reviewed: false,
  });
  return {
    document_type: 'CLAIM_PACKET',
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

export function briefWorkbench(claimedBy: string | null = null): Workbench {
  const field = (name: string, value: unknown, page: number) => ({
    name,
    value,
    confidence: '0.9300',
    source_page: page,
    evidence_excerpt: `Evidence for ${name}`,
    reviewed: false,
  });
  return {
    document_type: 'CAMPAIGN_BRIEF',
    item: queueItem({
      review_task_id: 'task-b12',
      document_id: 'doc-b12',
      document_type: 'CAMPAIGN_BRIEF',
      claimed_by_user_id: claimedBy,
      original_filename: 'B12_brief.pdf',
      summary: {
        reference: 'SOW-1001',
        party: 'Harbor & Vine Coffee Co. · Vine Cold Brew',
        subject: 'Cold Brew New Year',
        event_label: 'Launch',
        event_date: '2027-01-05',
        context: 'Retail units sold',
        amount: '40000',
        item_count: 2,
      },
      route: {
        review_queue: 'RUSH_REVIEW',
        reason_code: 'TIMELINE_AT_RISK',
        flags: ['TIMELINE_AT_RISK', 'CORRESPONDENCE_CONFLICT'],
        routed_by: 'RULES',
        routing_version: 'brief-routing-v1',
      },
    }),
    review_cycle: 1,
    rules: [
      {
        rule_id: 'CB-008',
        name: 'Every deliverable has its minimum production lead time',
        outcome: 'REVIEW',
        reason_code: 'TIMELINE_AT_RISK',
      },
      {
        rule_id: 'CB-001',
        name: 'The brand is covered by a tenant statement of work',
        outcome: 'PASS',
        reason_code: null,
      },
    ],
    fields: [
      field('campaign_name', 'Cold Brew New Year', 1),
      field('launch_date', '2027-01-05', 1),
      field('launch_date_in_correspondence', '2026-11-30', 2),
      field('budget_total', '40000', 1),
      {
        ...field(
          'deliverables',
          [
            {
              asset_type: 'VIDEO_15',
              quantity: 1,
              description: 'New Year reset spot',
              due_date: '2026-12-22',
            },
            {
              asset_type: 'SOCIAL_STATIC',
              quantity: 8,
              description: 'Routine posts',
              due_date: '2026-12-28',
            },
          ],
          1,
        ),
        items: [
          { confidence: '0.9300', source_page: 1, evidence_excerpt: '1 x reset spot' },
          { confidence: '0.9300', source_page: 1, evidence_excerpt: '8 x posts' },
        ],
      },
      { ...field('deliverables_added_in_correspondence', [], 2), items: [] },
    ],
    segments: [
      { page_number: 1, label: 'BRIEF', confidence: '0.95' },
      { page_number: 2, label: 'CORRESPONDENCE', confidence: '0.95' },
    ],
    reroutes: [],
  };
}
