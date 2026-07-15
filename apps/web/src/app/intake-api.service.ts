import { HttpClient, HttpEvent, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type UserRole = 'SUBMITTER' | 'REVIEWER' | 'APPROVER' | 'OPERATIONS_ADMIN' | 'AUDITOR';
export type ReviewDecision = 'APPROVE' | 'REJECT' | 'REQUEST_CORRECTION';

export interface SessionIdentity {
  readonly tenant_id: string;
  readonly user_id: string;
  readonly role: UserRole;
  readonly subject: string;
}

export interface DocumentCreated {
  readonly document_id: string;
  readonly document_version_id: string;
  readonly status: 'RECEIVED';
  readonly duplicate_hash_detected: boolean;
  readonly correlation_id: string;
  readonly graph_run_id: string;
  readonly graph_run_status: 'REQUESTED';
}

export interface DocumentSummary {
  readonly id: string;
  readonly status: string;
  readonly processing_stage: string;
  readonly graph_status: string;
  readonly duplicate_content: boolean;
  readonly created_at: string;
  readonly review_task_id: string | null;
}

export interface ExtractionField {
  readonly name: string;
  readonly value: unknown;
  readonly confidence: string;
  readonly page_number: number | null;
  readonly evidence_excerpt: string;
  readonly extraction_strategy: string;
  readonly provider: string;
  readonly model: string;
}

export interface DocumentDetail {
  readonly document: DocumentSummary;
  readonly graph_run_id: string;
  readonly graph_version: string;
  readonly correlation_id: string;
  readonly pdf_content_url: string;
  readonly agent_tools: readonly Record<string, unknown>[];
  readonly advisory_recommendation: Record<string, unknown> | null;
  readonly extraction_candidate_id: string | null;
  readonly extracted_fields: readonly ExtractionField[];
  readonly authoritative_validation: readonly Record<string, unknown>[];
  readonly confidence: Record<string, unknown> | null;
  readonly review: Record<string, unknown> | null;
  readonly audit_timeline: readonly Record<string, unknown>[];
}

export interface ReviewTask {
  readonly id: string;
  readonly document_id: string;
  readonly graph_run_id: string;
  readonly status: string;
  readonly cycle: number;
  readonly claimed_by_user_id: string | null;
  readonly extraction_candidate_id: string | null;
  readonly validation_run_id: string | null;
  readonly confidence_run_id: string | null;
  readonly decision: string | null;
}

export interface FieldCorrection {
  readonly field: string;
  readonly new_value: string | null;
  readonly reason: string;
}

@Injectable({ providedIn: 'root' })
export class IntakeApiService {
  private readonly http = inject(HttpClient);

  upload(
    file: File,
    idempotencyKey: string,
    accessToken: string,
  ): Observable<HttpEvent<DocumentCreated>> {
    const body = new FormData();
    body.append('file', file, file.name);
    return this.http.request<DocumentCreated>('POST', '/api/v1/documents', {
      body,
      headers: this.headers(accessToken).set('Idempotency-Key', idempotencyKey),
      observe: 'events',
      reportProgress: true,
    });
  }

  session(token: string): Observable<SessionIdentity> {
    return this.http.get<SessionIdentity>('/api/v1/session', { headers: this.headers(token) });
  }

  documents(token: string): Observable<readonly DocumentSummary[]> {
    return this.http.get<readonly DocumentSummary[]>('/api/v1/documents', {
      headers: this.headers(token),
    });
  }

  document(id: string, token: string): Observable<DocumentDetail> {
    return this.http.get<DocumentDetail>(`/api/v1/documents/${id}`, {
      headers: this.headers(token),
    });
  }

  pdf(id: string, token: string): Observable<Blob> {
    return this.http.get(`/api/v1/documents/${id}/content`, {
      headers: this.headers(token),
      responseType: 'blob',
    });
  }

  reviewTasks(token: string): Observable<readonly ReviewTask[]> {
    return this.http.get<readonly ReviewTask[]>('/api/v1/review-tasks', {
      headers: this.headers(token),
    });
  }

  claim(taskId: string, token: string): Observable<ReviewTask> {
    return this.http.post<ReviewTask>(`/api/v1/review-tasks/${taskId}/claim`, null, {
      headers: this.headers(token),
    });
  }

  unclaim(taskId: string, token: string): Observable<ReviewTask> {
    return this.http.post<ReviewTask>(`/api/v1/review-tasks/${taskId}/unclaim`, null, {
      headers: this.headers(token),
    });
  }

  complete(
    taskId: string,
    decision: ReviewDecision,
    reason: string,
    fieldCorrections: readonly FieldCorrection[],
    token: string,
  ): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(
      `/api/v1/review-tasks/${taskId}/complete`,
      { decision, reason, field_corrections: fieldCorrections },
      { headers: this.headers(token) },
    );
  }

  private headers(token: string): HttpHeaders {
    return new HttpHeaders({ Authorization: `Bearer ${token}` });
  }
}
