import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type PackType = 'CLAIM_PACKET' | 'CAMPAIGN_BRIEF';

export type ReviewQueue =
  | 'FAST_TRACK'
  | 'DESK_ADJUSTER'
  | 'COVERAGE_REVIEW'
  | 'SIU_REFERRAL'
  | 'BODILY_INJURY_ADJUSTER'
  | 'TOTAL_LOSS_REVIEW'
  | 'BELOW_DEDUCTIBLE_NOTICE'
  | 'DUPLICATE_REVIEW'
  | 'READY_FOR_PLANNING'
  | 'NEEDS_CLIENT_INFO'
  | 'SCOPE_REVIEW'
  | 'LEGAL_REVIEW'
  | 'RUSH_REVIEW'
  | 'TRAFFIC_REVIEW';

export type ServiceLevelStatus = 'ON_TRACK' | 'DUE_SOON' | 'OVERDUE' | 'MET' | 'MISSED';

export interface QueueRoute {
  readonly review_queue: ReviewQueue;
  readonly reason_code: string;
  readonly flags: readonly string[];
  readonly routed_by: 'RULES' | 'REVIEWER';
  readonly routing_version: string | null;
}

export interface ServiceLevel {
  readonly target_minutes: number;
  readonly status: ServiceLevelStatus;
  readonly minutes_remaining: number;
}

export interface ItemSummary {
  readonly reference: string | null;
  readonly party: string | null;
  readonly subject: string | null;
  readonly event_label: string;
  readonly event_date: string | null;
  readonly context: string | null;
  readonly amount: string | null;
  readonly item_count: number | null;
}

export interface ReviewQueueItem {
  readonly review_task_id: string;
  readonly document_id: string;
  readonly document_type: PackType;
  readonly task_status: string;
  readonly claimed_by_user_id: string | null;
  readonly original_filename: string | null;
  readonly summary: ItemSummary;
  readonly route: QueueRoute;
  readonly uploaded_at: string;
  readonly age_minutes: number;
  readonly service_level: ServiceLevel;
}

export interface ReviewQueueView {
  readonly document_type: PackType;
  readonly generated_at: string;
  readonly queue: ReviewQueue | null;
  readonly items: readonly ReviewQueueItem[];
  readonly counts_by_queue: Readonly<Record<string, number>>;
  readonly counts_by_service_level: Readonly<Record<string, number>>;
}

export interface RuleOutcome {
  readonly rule_id: string;
  readonly name: string | null;
  readonly outcome: string;
  readonly reason_code: string | null;
}

export interface ItemEvidence {
  readonly confidence: string | null;
  readonly source_page: number | null;
  readonly evidence_excerpt: string | null;
}

export interface WorkField {
  readonly name: string;
  readonly value: unknown;
  readonly confidence: string | null;
  readonly source_page: number | null;
  readonly evidence_excerpt: string | null;
  readonly reviewed: boolean;
  readonly items?: readonly ItemEvidence[] | null;
}

export interface PageSegment {
  readonly page_number: number;
  readonly label: string;
  readonly confidence: string;
}

export interface Reroute {
  readonly from_queue: ReviewQueue;
  readonly to_queue: ReviewQueue;
  readonly reason: string;
  readonly rerouted_by_user_id: string;
  readonly created_at: string;
}

export interface Workbench {
  readonly document_type: PackType;
  readonly item: ReviewQueueItem;
  readonly review_cycle: number;
  readonly rules: readonly RuleOutcome[];
  readonly fields: readonly WorkField[];
  readonly segments: readonly PageSegment[];
  readonly reroutes: readonly Reroute[];
}

@Injectable({ providedIn: 'root' })
export class ReviewApiService {
  private readonly http = inject(HttpClient);

  queue(documentType: PackType, token: string): Observable<ReviewQueueView> {
    return this.http.get<ReviewQueueView>('/api/v1/review-queues', {
      params: { document_type: documentType },
      headers: this.headers(token),
      withCredentials: true,
    });
  }

  workbench(taskId: string, token: string): Observable<Workbench> {
    return this.http.get<Workbench>(`/api/v1/review-queues/tasks/${taskId}`, {
      headers: this.headers(token),
      withCredentials: true,
    });
  }

  reroute(
    taskId: string,
    toQueue: ReviewQueue,
    reason: string,
    token: string,
  ): Observable<QueueRoute> {
    return this.http.post<QueueRoute>(
      `/api/v1/review-queues/tasks/${taskId}/reroute`,
      { to_queue: toQueue, reason },
      { headers: this.headers(token), withCredentials: true },
    );
  }

  private headers(token: string): HttpHeaders {
    const headers = new HttpHeaders();
    return token ? headers.set('Authorization', `Bearer ${token}`) : headers;
  }
}
