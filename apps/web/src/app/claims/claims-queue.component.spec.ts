import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { queueItem, queueView } from './claims-fixtures.spec-helpers';
import { ClaimsQueueComponent } from './claims-queue.component';

describe('ClaimsQueueComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ClaimsQueueComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  function render(): HTMLElement {
    const fixture = TestBed.createComponent(ClaimsQueueComponent);
    fixture.componentRef.setInput('token', 'reviewer-token');
    fixture.detectChanges();
    const request = http.expectOne('/api/v1/claims/queue');
    expect(request.request.headers.get('Authorization')).toBe('Bearer reviewer-token');
    request.flush(
      queueView([
        queueItem(),
        queueItem({
          review_task_id: 'task-c01',
          original_filename: 'C01_packet.pdf',
          named_insured: 'Marisol Vega',
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
    const fixture = TestBed.createComponent(ClaimsQueueComponent);
    fixture.detectChanges();
    http.expectOne('/api/v1/claims/queue').flush(queueView([queueItem()]));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    const fastTrack = Array.from(element.querySelectorAll<HTMLButtonElement>('.tabs button')).find(
      (button) => button.textContent.includes('Fast track'),
    );
    fastTrack?.click();
    fixture.detectChanges();

    expect(fastTrack?.getAttribute('aria-pressed')).toBe('true');
    expect(element.textContent).toContain('No open claims in this queue.');
  });
});
