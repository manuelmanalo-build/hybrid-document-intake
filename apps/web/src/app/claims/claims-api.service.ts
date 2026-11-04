import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type ReviewQueue =
  | 'FAST_TRACK'
  | 'DESK_ADJUSTER'
  | 'COVERAGE_REVIEW'
  | 'SIU_REFERRAL'
  | 'BODILY_INJURY_ADJUSTER'
  | 'TOTAL_LOSS_REVIEW'
  | 'BELOW_DEDUCTIBLE_NOTICE'
  | 'DUPLICATE_REVIEW';

export type ServiceLevelStatus = 'ON_TRACK' | 'DUE_SOON' | 'OVERDUE' | 'MET' | 'MISSED';

export interface ClaimRoute {
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

export interface ClaimQueueItem {
  readonly review_task_id: string;
  readonly document_id: string;
  readonly task_status: string;
  readonly claimed_by_user_id: string | null;
  readonly original_filename: string | null;
  readonly policy_number: string | null;
  readonly named_insured: string | null;
  readonly vehicle: string | null;
  readonly loss_date: string | null;
  readonly cause_of_loss: string | null;
  readonly estimate_total: string | null;
  readonly route: ClaimRoute;
  readonly uploaded_at: string;
  readonly age_minutes: number;
  readonly service_level: ServiceLevel;
}

export interface ClaimQueueView {
  readonly generated_at: string;
  readonly queue: ReviewQueue | null;
  readonly items: readonly ClaimQueueItem[];
  readonly counts_by_queue: Readonly<Record<string, number>>;
  readonly counts_by_service_level: Readonly<Record<string, number>>;
}

export interface RuleOutcome {
  readonly rule_id: string;
  readonly name: string | null;
  readonly outcome: string;
  readonly reason_code: string | null;
}

export interface ClaimField {
  readonly name: string;
  readonly value: unknown;
  readonly confidence: string | null;
  readonly source_page: number | null;
  readonly evidence_excerpt: string | null;
  readonly reviewed: boolean;
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

export interface ClaimWorkbench {
  readonly item: ClaimQueueItem;
  readonly review_cycle: number;
  readonly rules: readonly RuleOutcome[];
  readonly fields: readonly ClaimField[];
  readonly segments: readonly PageSegment[];
  readonly reroutes: readonly Reroute[];
}

@Injectable({ providedIn: 'root' })
export class ClaimsApiService {
  private readonly http = inject(HttpClient);

  queue(token: string): Observable<ClaimQueueView> {
    return this.http.get<ClaimQueueView>('/api/v1/claims/queue', {
      headers: this.headers(token),
      withCredentials: true,
    });
  }

  workbench(taskId: string, token: string): Observable<ClaimWorkbench> {
    return this.http.get<ClaimWorkbench>(`/api/v1/claims/${taskId}`, {
      headers: this.headers(token),
      withCredentials: true,
    });
  }

  reroute(
    taskId: string,
    toQueue: ReviewQueue,
    reason: string,
    token: string,
  ): Observable<ClaimRoute> {
    return this.http.post<ClaimRoute>(
      `/api/v1/claims/${taskId}/reroute`,
      { to_queue: toQueue, reason },
      { headers: this.headers(token), withCredentials: true },
    );
  }

  private headers(token: string): HttpHeaders {
    const headers = new HttpHeaders();
    return token ? headers.set('Authorization', `Bearer ${token}`) : headers;
  }
}
