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

export function cdpWorkbench(claimedBy: string | null = null): Workbench {
  const field = (name: string, value: unknown, page: number) => ({
    name,
    value,
    confidence: '0.9300',
    source_page: page,
    evidence_excerpt: `Evidence for ${name}`,
    reviewed: false,
  });
  return {
    document_type: 'CDP_ONBOARDING',
    context: {
      attributes: {
        customer_id: {
          entity: 'PROFILE',
          data_type: 'STRING',
          personal_class: 'NONE',
          identity_key: true,
        },
        email_address: {
          entity: 'PROFILE',
          data_type: 'EMAIL',
          personal_class: 'DIRECT_IDENTIFIER',
          identity_key: true,
        },
        loyalty_tier: {
          entity: 'PROFILE',
          data_type: 'STRING',
          personal_class: 'NONE',
          identity_key: false,
        },
      },
      compatible_types: { STRING: ['STRING', 'EMAIL', 'PHONE'], DATE: ['DATE'] },
      scan_findings: [
        {
          page_number: 1,
          identifier_type: 'EMAIL',
          occurrences: 2,
          scanner_version: 'pii-scanner-v1',
        },
        {
          page_number: 3,
          identifier_type: 'EMAIL',
          occurrences: 6,
          scanner_version: 'pii-scanner-v1',
        },
        {
          page_number: 3,
          identifier_type: 'PHONE',
          occurrences: 6,
          scanner_version: 'pii-scanner-v1',
        },
      ],
    },
    item: queueItem({
      review_task_id: 'task-d11',
      document_id: 'doc-d11',
      document_type: 'CDP_ONBOARDING',
      claimed_by_user_id: claimedBy,
      original_filename: 'D11_onboarding.pdf',
      summary: {
        reference: 'DPA-2002',
        party: 'Lumen Home',
        subject: 'Braze customer profiles',
        event_label: 'Go-live',
        event_date: '2026-12-14',
        context: 'Braze',
        amount: null,
        item_count: 3,
      },
      route: {
        review_queue: 'PRIVACY_REVIEW',
        reason_code: 'PERSONAL_DATA_FINDINGS',
        flags: ['UNDECLARED_PERSONAL_DATA', 'UNMASKED_IDENTIFIERS_IN_PACKET'],
        routed_by: 'RULES',
        routing_version: 'cdp-routing-v1',
      },
    }),
    review_cycle: 1,
    rules: [
      {
        rule_id: 'CD-008',
        name: 'No unmasked identifiers',
        outcome: 'REVIEW',
        reason_code: 'UNMASKED_IDENTIFIERS_IN_PACKET',
      },
    ],
    fields: [
      field('source_name', 'Braze customer profiles', 1),
      field('declares_no_personal_data', true, 1),
      {
        ...field(
          'mappings',
          [
            {
              source_field: 'external_id',
              source_type: 'STRING',
              proposed_attribute: 'customer_id',
              client_marked_personal: false,
            },
            {
              source_field: 'contact',
              source_type: 'STRING',
              proposed_attribute: 'email_address',
              client_marked_personal: false,
            },
            {
              source_field: 'signup',
              source_type: 'DATE',
              proposed_attribute: 'loyalty_tier',
              client_marked_personal: false,
            },
          ],
          2,
        ),
        items: [
          { confidence: '0.9', source_page: 2, evidence_excerpt: 'external_id' },
          { confidence: '0.9', source_page: 2, evidence_excerpt: 'contact' },
          { confidence: '0.9', source_page: 2, evidence_excerpt: 'signup' },
        ],
      },
    ],
    segments: [
      { page_number: 1, label: 'ONBOARDING_FORM', confidence: '0.95' },
      { page_number: 2, label: 'DATA_DICTIONARY', confidence: '0.95' },
      { page_number: 3, label: 'SAMPLE_EXTRACT', confidence: '0.95' },
    ],
    reroutes: [],
  };
}
