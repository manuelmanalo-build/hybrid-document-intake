import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { IntakeApiService } from './intake-api.service';

describe('IntakeApiService', () => {
  let service: IntakeApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(IntakeApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('uploads multipart content with the preserved idempotency key and progress enabled', () => {
    service
      .upload(new File(['%PDF-1.7'], 'invoice.pdf'), 'stable-key', 'submitter-token')
      .subscribe();

    const request = http.expectOne('/api/v1/documents');
    expect(request.request.method).toBe('POST');
    expect(request.request.reportProgress).toBe(true);
    expect(request.request.headers.get('Idempotency-Key')).toBe('stable-key');
    expect(request.request.headers.get('Authorization')).toBe('Bearer submitter-token');
    expect(request.request.body).toBeInstanceOf(FormData);
    expect(request.request.withCredentials).toBe(true);
  });

  it('creates an HttpOnly-backed demo role session without a bearer credential', () => {
    service.connectDemo('AUDITOR').subscribe();

    const request = http.expectOne('/api/v1/demo-auth/session');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ role: 'AUDITOR' });
    expect(request.request.headers.has('Authorization')).toBe(false);
    expect(request.request.withCredentials).toBe(true);
  });

  it('uses the demo session cookie when no bearer token is supplied', () => {
    service.documents('').subscribe();

    const request = http.expectOne('/api/v1/documents');
    expect(request.request.headers.has('Authorization')).toBe(false);
    expect(request.request.withCredentials).toBe(true);
  });

  it('sends review corrections and decisions only to FastAPI', () => {
    service
      .complete(
        'task-1',
        'REQUEST_CORRECTION',
        'Verify total',
        [{ field: 'total', new_value: '1360.00', reason: 'Confirmed from PDF' }],
        'reviewer-token',
      )
      .subscribe();

    const request = http.expectOne('/api/v1/review-tasks/task-1/complete');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      decision: 'REQUEST_CORRECTION',
      reason: 'Verify total',
      field_corrections: [{ field: 'total', new_value: '1360.00', reason: 'Confirmed from PDF' }],
    });
  });
});
