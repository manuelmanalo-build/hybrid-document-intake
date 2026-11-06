import { PackType, ReviewQueue, ServiceLevelStatus } from './review-api.service';

export const QUEUE_LABELS: Readonly<Record<ReviewQueue, string>> = {
  FAST_TRACK: 'Fast track',
  DESK_ADJUSTER: 'Desk adjuster',
  COVERAGE_REVIEW: 'Coverage review',
  SIU_REFERRAL: 'SIU referral',
  BODILY_INJURY_ADJUSTER: 'Bodily injury',
  TOTAL_LOSS_REVIEW: 'Total loss review',
  BELOW_DEDUCTIBLE_NOTICE: 'Below deductible',
  DUPLICATE_REVIEW: 'Duplicate review',
  READY_FOR_PLANNING: 'Ready for planning',
  NEEDS_CLIENT_INFO: 'Needs client info',
  SCOPE_REVIEW: 'Scope review',
  LEGAL_REVIEW: 'Legal review',
  RUSH_REVIEW: 'Rush review',
  TRAFFIC_REVIEW: 'Traffic review',
  READY_TO_BUILD: 'Ready to build',
  AGREEMENT_REVIEW: 'Agreement review',
  PRIVACY_REVIEW: 'Privacy review',
  MAPPING_REVIEW: 'Mapping review',
  ONBOARDING_REVIEW: 'Onboarding review',
};

export type FlagTone = 'attention' | 'info' | 'plain';

const FLAGS: Readonly<Partial<Record<string, readonly [string, FlagTone]>>> = {
  DUPLICATE_CLAIM: ['Possible duplicate', 'attention'],
  LOSS_OUTSIDE_POLICY_TERM: ['Loss outside policy term', 'attention'],
  POLICY_CANCELLED_BEFORE_LOSS: ['Policy cancelled before loss', 'attention'],
  VEHICLE_NOT_ON_POLICY: ['Vehicle not on policy', 'attention'],
  VIN_DISCREPANCY: ['VIN mismatch', 'attention'],
  VIN_REQUIRED: ['VIN missing', 'attention'],
  LATE_REPORTING: ['Reported late', 'attention'],
  LOSS_NEAR_INCEPTION: ['Soon after policy start', 'attention'],
  INJURY_INDICATED: ['Injury indicated', 'attention'],
  INJURY_CONFLICT: ['Injury in email, not on form', 'attention'],
  POSSIBLE_TOTAL_LOSS: ['Possible total loss', 'attention'],
  AT_OR_BELOW_DEDUCTIBLE: ['At or below deductible', 'info'],
  THIRD_PARTY_INVOLVED: ['Third party', 'info'],
  ESTIMATE_ARITHMETIC_MISMATCH: ["Estimate doesn't add up", 'attention'],
  CRITICAL_FIELD_MISSING: ['Key field missing', 'attention'],
  CRITICAL_FIELD_LOW_CONFIDENCE: ['Low-confidence field', 'attention'],
  UNSUPPORTED_SEGMENT: ['Unrecognized page', 'info'],
  POLICY_NOT_FOUND: ['Policy not found', 'attention'],
  POLICY_NUMBER_REQUIRED: ['Policy number missing', 'attention'],
  LOSS_DATE_INVALID: ['Loss date invalid', 'attention'],
  SOW_NOT_FOUND: ['Brand not on any SOW', 'attention'],
  SOW_NOT_ACTIVE_AT_LAUNCH: ['SOW not active at launch', 'attention'],
  BRIEF_INCOMPLETE: ['Brief incomplete', 'attention'],
  KPI_NOT_MEASURABLE: ['KPI has no target number', 'attention'],
  BUDGET_MISSING: ['Budget missing', 'attention'],
  OVER_SOW_BUDGET: ['Over remaining SOW fees', 'attention'],
  OUT_OF_SCOPE_SERVICE: ['Service not in SOW', 'attention'],
  TIMELINE_AT_RISK: ['Lead time at risk', 'attention'],
  CORRESPONDENCE_CONFLICT: ['Email changes the brief', 'attention'],
  REGULATED_CONTENT: ['Regulated category', 'attention'],
  DUPLICATE_BRIEF: ['Possible duplicate', 'attention'],
  DATES_INVALID: ['Dates out of order', 'attention'],
  AGREEMENT_NOT_FOUND: ['No data agreement', 'attention'],
  AGREEMENT_NOT_ACTIVE: ['Agreement not active at go-live', 'attention'],
  REGION_NOT_PERMITTED: ['Region not permitted', 'attention'],
  REQUEST_INCOMPLETE: ['Request incomplete', 'attention'],
  CONNECTOR_UNSUPPORTED: ['Connector or cadence unsupported', 'attention'],
  NO_IDENTITY_KEY: ['No identity key mapped', 'attention'],
  MAPPING_INVALID: ['Mapping invalid', 'attention'],
  UNDECLARED_PERSONAL_DATA: ['Undeclared personal data', 'attention'],
  UNMASKED_IDENTIFIERS_IN_PACKET: ['Unmasked identifiers in sample', 'attention'],
  SAMPLE_NOT_SCANNABLE: ['Sample could not be scanned', 'attention'],
  LAWFUL_BASIS_MISSING: ['Lawful basis missing', 'attention'],
  SENSITIVE_DATA_NOT_PERMITTED: ['Sensitive data not permitted', 'attention'],
  RETENTION_EXCEEDS_AGREEMENT: ['Retention over agreement', 'attention'],
  DUPLICATE_REQUEST: ['Possible duplicate', 'attention'],
};

export function flagLabel(code: string): { readonly label: string; readonly tone: FlagTone } {
  const known = FLAGS[code];
  return known
    ? { label: known[0], tone: known[1] }
    : { label: code.replaceAll('_', ' ').toLowerCase(), tone: 'plain' };
}

export const SERVICE_LEVEL_LABELS: Readonly<Record<ServiceLevelStatus, string>> = {
  ON_TRACK: 'On track',
  DUE_SOON: 'Due soon',
  OVERDUE: 'Overdue',
  MET: 'Met',
  MISSED: 'Missed',
};

export const SEGMENT_LABELS: Readonly<Record<string, string>> = {
  LOSS_NOTICE: 'Loss notice',
  REPAIR_ESTIMATE: 'Repair estimate',
  POLICE_REPORT: 'Police report',
  CORRESPONDENCE: 'Correspondence',
  BRIEF: 'Brief',
  MEDIA_PLAN: 'Media plan',
  ONBOARDING_FORM: 'Onboarding form',
  DATA_DICTIONARY: 'Data dictionary',
  SAMPLE_EXTRACT: 'Sample extract',
  PRIVACY_ADDENDUM: 'Privacy addendum',
  OTHER: 'Other',
};

export function formatMinutes(minutes: number): string {
  const value = Math.abs(minutes);
  if (value < 60) return `${String(value)}m`;
  const hours = Math.floor(value / 60);
  if (hours < 48) return `${String(hours)}h ${String(value % 60).padStart(2, '0')}m`;
  return `${String(Math.floor(hours / 24))}d ${String(hours % 24)}h`;
}

export function formatMoney(value: string | null): string {
  if (value === null) return '—';
  const amount = Number(value);
  return Number.isFinite(amount)
    ? amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
    : value;
}

export function formatDate(value: string | null): string {
  if (value === null) return '—';
  const [year, month, day] = value.split('-');
  return year && month && day ? `${month}/${day}/${year}` : value;
}

export type FieldKind = 'text' | 'boolean' | 'coverage' | 'date' | 'money' | 'list' | 'mapping';

export interface PackUi {
  readonly type: PackType;
  readonly label: string;
  readonly singular: string;
  readonly queues: readonly ReviewQueue[];
  readonly columns: {
    readonly item: string;
    readonly party: string;
    readonly event: string;
    readonly amount: string;
  };
  readonly groups: readonly (readonly [string, readonly string[]])[];
  readonly labels: Readonly<Record<string, string>>;
  readonly kinds: Readonly<Record<string, FieldKind>>;
  readonly cleanLabel: string;
  /** What a queue row's item count counts, for example deliverables or mappings. */
  readonly itemNoun: string;
  /** Field names that disagree across the packet's documents and should be outlined. */
  readonly conflicts: (value: (name: string) => unknown) => readonly string[];
  /** The page to open first, for example the email that changes the brief. */
  readonly focusField: string;
}

const CLAIM_UI: PackUi = {
  type: 'CLAIM_PACKET',
  label: 'Claims',
  singular: 'claim',
  queues: [
    'FAST_TRACK',
    'DESK_ADJUSTER',
    'COVERAGE_REVIEW',
    'BODILY_INJURY_ADJUSTER',
    'SIU_REFERRAL',
    'TOTAL_LOSS_REVIEW',
    'BELOW_DEDUCTIBLE_NOTICE',
    'DUPLICATE_REVIEW',
  ],
  columns: { item: 'Claim', party: 'Insured and vehicle', event: 'Loss', amount: 'Estimate' },
  groups: [
    ['Policy', ['policy_number', 'named_insured', 'coverage']],
    [
      'Loss',
      ['loss_date', 'report_date', 'cause_of_loss', 'police_report_number', 'third_party_involved'],
    ],
    ['Injury', ['injury_indicated_on_form', 'injury_mentioned_in_correspondence']],
    [
      'Vehicle',
      ['vehicle_year', 'vehicle_make', 'vehicle_model', 'vin_reported', 'vin_on_estimate'],
    ],
    [
      'Estimate',
      [
        'estimate_number',
        'estimate_parts',
        'estimate_labor',
        'estimate_materials',
        'estimate_tax',
        'estimate_total',
      ],
    ],
  ],
  labels: {
    policy_number: 'Policy number',
    named_insured: 'Named insured',
    coverage: 'Coverage',
    loss_date: 'Date of loss',
    report_date: 'Reported',
    cause_of_loss: 'Cause',
    police_report_number: 'Police report',
    third_party_involved: 'Third party involved',
    injury_indicated_on_form: 'On loss notice',
    injury_mentioned_in_correspondence: 'In correspondence',
    vehicle_year: 'Year',
    vehicle_make: 'Make',
    vehicle_model: 'Model',
    vin_reported: 'VIN on notice',
    vin_on_estimate: 'VIN on estimate',
    estimate_number: 'Estimate number',
    estimate_parts: 'Parts',
    estimate_labor: 'Labor',
    estimate_materials: 'Paint materials',
    estimate_tax: 'Tax',
    estimate_total: 'Grand total',
  },
  kinds: {
    injury_indicated_on_form: 'boolean',
    injury_mentioned_in_correspondence: 'boolean',
    third_party_involved: 'boolean',
    coverage: 'coverage',
    loss_date: 'date',
    report_date: 'date',
    estimate_parts: 'money',
    estimate_labor: 'money',
    estimate_materials: 'money',
    estimate_tax: 'money',
    estimate_total: 'money',
  },
  cleanLabel: 'Clean · within limit',
  itemNoun: 'items',
  conflicts: (value) => {
    const names: string[] = [];
    if (
      value('injury_indicated_on_form') === false &&
      value('injury_mentioned_in_correspondence') === true
    ) {
      names.push('injury_indicated_on_form', 'injury_mentioned_in_correspondence');
    }
    const reported = value('vin_reported');
    const estimate = value('vin_on_estimate');
    if (typeof reported === 'string' && typeof estimate === 'string' && reported !== estimate) {
      names.push('vin_reported', 'vin_on_estimate');
    }
    return names;
  },
  focusField: 'injury_mentioned_in_correspondence',
};

const BRIEF_UI: PackUi = {
  type: 'CAMPAIGN_BRIEF',
  label: 'Briefs',
  singular: 'brief',
  queues: [
    'READY_FOR_PLANNING',
    'NEEDS_CLIENT_INFO',
    'SCOPE_REVIEW',
    'LEGAL_REVIEW',
    'RUSH_REVIEW',
    'TRAFFIC_REVIEW',
    'DUPLICATE_REVIEW',
  ],
  columns: { item: 'Brief', party: 'Client and campaign', event: 'Launch', amount: 'Budget' },
  groups: [
    [
      'Client and contract',
      ['client_name', 'brand', 'campaign_name', 'sow_number_stated', 'approver'],
    ],
    ['Goals', ['objective', 'primary_kpi', 'kpi_target', 'audience', 'markets']],
    [
      'Timing and budget',
      ['received_date', 'launch_date', 'launch_date_in_correspondence', 'end_date', 'budget_total'],
    ],
    ['Mandatories', ['mandatories']],
    ['Deliverables', ['deliverables', 'deliverables_added_in_correspondence']],
  ],
  labels: {
    client_name: 'Client',
    brand: 'Brand',
    campaign_name: 'Campaign',
    sow_number_stated: 'SOW stated',
    approver: 'Approver',
    objective: 'Objective',
    primary_kpi: 'Primary KPI',
    kpi_target: 'KPI target',
    audience: 'Audience',
    markets: 'Markets',
    received_date: 'Received',
    launch_date: 'Launch (brief)',
    launch_date_in_correspondence: 'Launch (email)',
    end_date: 'End',
    budget_total: 'Budget',
    mandatories: 'Mandatories and legal',
    deliverables: 'Requested in the brief',
    deliverables_added_in_correspondence: 'Added in email',
  },
  kinds: {
    received_date: 'date',
    launch_date: 'date',
    launch_date_in_correspondence: 'date',
    end_date: 'date',
    budget_total: 'money',
    deliverables: 'list',
    deliverables_added_in_correspondence: 'list',
  },
  cleanLabel: 'Complete and in scope',
  itemNoun: 'deliverables',
  conflicts: (value) => {
    const names: string[] = [];
    const moved = value('launch_date_in_correspondence');
    if (typeof moved === 'string' && moved !== value('launch_date')) {
      names.push('launch_date', 'launch_date_in_correspondence');
    }
    const added = value('deliverables_added_in_correspondence');
    if (Array.isArray(added) && added.length > 0)
      names.push('deliverables_added_in_correspondence');
    return names;
  },
  focusField: 'launch_date_in_correspondence',
};

const CDP_UI: PackUi = {
  type: 'CDP_ONBOARDING',
  label: 'Onboarding',
  singular: 'onboarding request',
  queues: [
    'READY_TO_BUILD',
    'NEEDS_CLIENT_INFO',
    'AGREEMENT_REVIEW',
    'PRIVACY_REVIEW',
    'MAPPING_REVIEW',
    'RUSH_REVIEW',
    'ONBOARDING_REVIEW',
    'DUPLICATE_REVIEW',
  ],
  columns: { item: 'Request', party: 'Client and source', event: 'Go-live', amount: 'Mappings' },
  groups: [
    [
      'Request',
      [
        'client_name',
        'agreement_number_stated',
        'source_name',
        'connector_type',
        'refresh_cadence',
        'data_region',
        'estimated_records',
      ],
    ],
    [
      'Privacy',
      [
        'declares_no_personal_data',
        'declares_sensitive_data',
        'lawful_basis',
        'consent_mechanism',
        'retention_days',
      ],
    ],
    ['Timing', ['received_date', 'requested_go_live', 'go_live_in_correspondence', 'data_owner']],
    ['Field mappings', ['mappings', 'fields_added_in_correspondence']],
  ],
  labels: {
    client_name: 'Client',
    agreement_number_stated: 'Agreement stated',
    source_name: 'Source',
    connector_type: 'Connector',
    refresh_cadence: 'Cadence',
    data_region: 'Data region',
    estimated_records: 'Estimated records',
    declares_no_personal_data: 'Client says no personal data',
    declares_sensitive_data: 'Client declares sensitive data',
    lawful_basis: 'Lawful basis',
    consent_mechanism: 'Consent mechanism',
    retention_days: 'Retention (days)',
    received_date: 'Received',
    requested_go_live: 'Go-live (form)',
    go_live_in_correspondence: 'Go-live (email)',
    data_owner: 'Data owner',
    mappings: 'From the data dictionary',
    fields_added_in_correspondence: 'Added in email',
  },
  kinds: {
    declares_no_personal_data: 'boolean',
    declares_sensitive_data: 'boolean',
    received_date: 'date',
    requested_go_live: 'date',
    go_live_in_correspondence: 'date',
    mappings: 'mapping',
    fields_added_in_correspondence: 'mapping',
  },
  cleanLabel: 'Mapped and cleared',
  itemNoun: 'mappings',
  conflicts: (value) => {
    const names: string[] = [];
    const moved = value('go_live_in_correspondence');
    if (typeof moved === 'string' && moved !== value('requested_go_live')) {
      names.push('requested_go_live', 'go_live_in_correspondence');
    }
    const added = value('fields_added_in_correspondence');
    if (Array.isArray(added) && added.length > 0) names.push('fields_added_in_correspondence');
    return names;
  },
  focusField: 'go_live_in_correspondence',
};

export const PACKS: Readonly<Record<PackType, PackUi>> = {
  CLAIM_PACKET: CLAIM_UI,
  CAMPAIGN_BRIEF: BRIEF_UI,
  CDP_ONBOARDING: CDP_UI,
};

export const IDENTIFIER_LABELS: Readonly<Record<string, readonly [string, string]>> = {
  EMAIL: ['email address', 'email addresses'],
  PHONE: ['phone number', 'phone numbers'],
  SSN: ['Social Security number', 'Social Security numbers'],
  CARD: ['card number', 'card numbers'],
};

export const ASSET_LABELS: Readonly<Record<string, string>> = {
  SOCIAL_STATIC: 'Social static post',
  SOCIAL_VIDEO: 'Social video',
  DISPLAY_SET: 'Display banner set',
  VIDEO_30: 'Video :30',
  VIDEO_15: 'Video :15',
  EMAIL: 'Email',
  LANDING_PAGE: 'Landing page',
  OOH_BOARD: 'Out-of-home board',
  AUDIO_30: 'Audio spot :30',
  SEARCH_ADS: 'Search ad set',
  INFLUENCER_KIT: 'Influencer kit',
  PRINT_AD: 'Print ad',
};
