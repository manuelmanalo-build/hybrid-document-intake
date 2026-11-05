import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DocumentDetail } from '../intake-api.service';
import { PacketIntakeComponent, progressFrom } from './packet-intake.component';

function detail(tools: readonly string[], extras: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    document: {
      id: 'doc-1',
      status: 'RECEIVED',
      processing_stage: 'RUNNING',
      graph_status: 'RUNNING',
      duplicate_content: false,
      created_at: '2026-10-22T08:00:00Z',
      review_task_id: null,
    },
    graph_run_id: 'run-1',
    graph_version: 'document-intake-v1',
    correlation_id: 'corr-1',
    pdf_content_url: '/api/v1/documents/doc-1/content',
    agent_tools: tools.map((tool) => ({ tool, status: 'COMPLETED' })),
    advisory_recommendation: null,
    extraction_candidate_id: null,
    extracted_fields: [],
    authoritative_validation: [],
    confidence: null,
    review: null,
    audit_timeline: [],
    ...extras,
  };
}

describe('claims intake progress', () => {
  it('maps pipeline evidence onto the six visible steps', () => {
    expect(progressFrom(detail(['inspect_document']))).toMatchObject({ done: 1 });
    expect(progressFrom(detail(['inspect_document', 'classify_document']))).toMatchObject({
      done: 2,
      result: 'Labeling pages',
    });
    expect(
      progressFrom(
        detail(['classify_document', 'segment_claim_packet', 'extract_claim_from_text'], {
          authoritative_validation: [{ rule_id: 'CR-001' }],
          review: { status: 'OPEN' },
        }),
      ),
    ).toMatchObject({ kind: 'READY', done: 6 });
  });

  it('tracks brief packets through the same steps', () => {
    expect(
      progressFrom(
        detail(['classify_document', 'segment_brief_packet', 'extract_brief_from_text']),
      ),
    ).toMatchObject({ done: 4, result: 'Checking rules' });
  });

  it('reports invoices and failures honestly', () => {
    expect(progressFrom(detail(['classify_document', 'extract_invoice_from_text']))).toMatchObject({
      result: 'Not a claim or brief: classified as an invoice',
    });
    const failed = detail([]);
    expect(
      progressFrom({ ...failed, document: { ...failed.document, graph_status: 'FAILED' } }),
    ).toMatchObject({ kind: 'PROBLEM' });
  });
});

describe('PacketIntakeComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PacketIntakeComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('uploads each chosen packet with its own idempotency key and reports rejections', () => {
    const fixture = TestBed.createComponent(PacketIntakeComponent);
    fixture.componentRef.setInput('token', 'submitter-token');
    fixture.componentRef.setInput('canSubmit', true);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    const input = element.querySelector<HTMLInputElement>('#packets');
    if (!input) throw new Error('file input missing');
    const files = [
      new File(['%PDF-1.7'], 'C01_packet.pdf', { type: 'application/pdf' }),
      new File(['not a pdf'], 'scan_0041.jpg', { type: 'image/jpeg' }),
    ];
    Object.defineProperty(input, 'files', { value: files });
    input.dispatchEvent(new Event('change'));

    const first = http.expectOne('/api/v1/documents');
    const firstKey = first.request.headers.get('Idempotency-Key');
    first.flush({
      document_id: 'doc-1',
      document_version_id: 'ver-1',
      status: 'RECEIVED',
      duplicate_hash_detected: false,
      correlation_id: 'corr-1',
      graph_run_id: 'run-1',
      graph_run_status: 'REQUESTED',
    });
    const second = http.expectOne('/api/v1/documents');
    expect(second.request.headers.get('Idempotency-Key')).not.toBe(firstKey);
    second.flush(
      { detail: 'The upload is not a PDF.' },
      { status: 400, statusText: 'Bad Request' },
    );
    fixture.detectChanges();

    expect(element.textContent).toContain('Classifying document');
    expect(element.textContent).toContain('Rejected: The upload is not a PDF.');
  });
});
