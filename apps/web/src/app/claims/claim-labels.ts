import { ReviewQueue, ServiceLevelStatus } from './claims-api.service';

export const QUEUE_ORDER: readonly ReviewQueue[] = [
  'FAST_TRACK',
  'DESK_ADJUSTER',
  'COVERAGE_REVIEW',
  'BODILY_INJURY_ADJUSTER',
  'SIU_REFERRAL',
  'TOTAL_LOSS_REVIEW',
  'BELOW_DEDUCTIBLE_NOTICE',
  'DUPLICATE_REVIEW',
];

export const QUEUE_LABELS: Readonly<Record<ReviewQueue, string>> = {
  FAST_TRACK: 'Fast track',
  DESK_ADJUSTER: 'Desk adjuster',
  COVERAGE_REVIEW: 'Coverage review',
  SIU_REFERRAL: 'SIU referral',
  BODILY_INJURY_ADJUSTER: 'Bodily injury',
  TOTAL_LOSS_REVIEW: 'Total loss review',
  BELOW_DEDUCTIBLE_NOTICE: 'Below deductible',
  DUPLICATE_REVIEW: 'Duplicate review',
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
