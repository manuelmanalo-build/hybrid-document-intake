import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { queueItem, queueView } from './work-fixtures.spec-helpers';
import { WorkQueueComponent } from './work-queue.component';

describe('WorkQueueComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WorkQueueComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  function render(): HTMLElement {
    const fixture = TestBed.createComponent(WorkQueueComponent);
    fixture.componentRef.setInput('token', 'reviewer-token');
    fixture.detectChanges();
    const request = http.expectOne(
      (r) => r.url === '/api/v1/review-queues' && r.params.get('document_type') === 'CLAIM_PACKET',
    );
    expect(request.request.headers.get('Authorization')).toBe('Bearer reviewer-token');
    request.flush(
      queueView([
        queueItem(),
        queueItem({
          review_task_id: 'task-c01',
          original_filename: 'C01_packet.pdf',
          summary: {
            reference: 'NFM-PA-4471200',
            party: 'Marisol Vega',
            subject: '2021 Honda CR-V EX',
            event_label: 'Loss',
            event_date: '2026-09-03',
            context: 'Hail',
            amount: '3241.87',
            item_count: null,
          },
          route: {
            review_queue: 'FAST_TRACK',
            reason_code: 'CLEAN_WITHIN_FAST_TRACK_LIMIT',
            flags: [],
            routed_by: 'RULES',
            routing_version: 'claim-routing-v1',
          },
          service_level: { target_minutes: 240, status: 'ON_TRACK', minutes_remaining: 110 },
        }),
      ]),
    );
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows queue counts, service levels and plain-language reasons', () => {
    const element = render();

    expect(element.textContent).toContain('1 past service level');
    expect(element.textContent).toContain('Injury in email, not on form');
    expect(element.textContent).toContain('Overdue');
    expect(element.textContent).toContain('35m past 2h 00m');
    expect(element.textContent).toContain('$2,415.90');
    expect(element.textContent).toContain('Clean · within limit');
  });

  it('filters rows by queue with pressed-state buttons', () => {
    const fixture = TestBed.createComponent(WorkQueueComponent);
    fixture.detectChanges();
    http
      .expectOne(
        (r) =>
          r.url === '/api/v1/review-queues' && r.params.get('document_type') === 'CLAIM_PACKET',
      )
      .flush(queueView([queueItem()]));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    const fastTrack = Array.from(element.querySelectorAll<HTMLButtonElement>('.tabs button')).find(
      (button) => button.textContent.includes('Fast track'),
    );
    fastTrack?.click();
    fixture.detectChanges();

    expect(fastTrack?.getAttribute('aria-pressed')).toBe('true');
    expect(element.textContent).toContain('Nothing open in this queue.');
  });

  it('switches to campaign briefs with their own queues and columns', () => {
    const fixture = TestBed.createComponent(WorkQueueComponent);
    fixture.detectChanges();
    http
      .expectOne((r) => r.params.get('document_type') === 'CLAIM_PACKET')
      .flush(queueView([queueItem()]));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    Array.from(element.querySelectorAll<HTMLButtonElement>('.types button'))
      .find((button) => button.textContent.includes('Briefs'))
      ?.click();
    http
      .expectOne((r) => r.params.get('document_type') === 'CAMPAIGN_BRIEF')
      .flush(
        queueView([
          queueItem({
            document_type: 'CAMPAIGN_BRIEF',
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
        ]),
      );
    fixture.detectChanges();

    expect(element.textContent).toContain('Client and campaign');
    expect(element.textContent).toContain('Rush review');
    expect(element.textContent).toContain('Email changes the brief');
    expect(element.textContent).toContain('2 deliverables');
    expect(element.textContent).not.toContain('Fast track');
  });

  it('offers onboarding requests with their own queues', () => {
    const fixture = TestBed.createComponent(WorkQueueComponent);
    fixture.componentRef.setInput('initialType', 'CDP_ONBOARDING');
    fixture.detectChanges();
    http.expectOne((r) => r.params.get('document_type') === 'CDP_ONBOARDING').flush(queueView([]));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.textContent).toContain('Privacy review');
    expect(element.textContent).toContain('Mapping review');
    expect(element.textContent).toContain('Client and source');
  });
});
