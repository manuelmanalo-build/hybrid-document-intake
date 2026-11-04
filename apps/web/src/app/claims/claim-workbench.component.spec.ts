import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ClaimWorkbenchComponent } from './claim-workbench.component';
import { workbench } from './claims-fixtures.spec-helpers';

describe('ClaimWorkbenchComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ClaimWorkbenchComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  function render(
    claimedBy: string | null,
    role = 'APPROVER',
  ): ComponentFixture<ClaimWorkbenchComponent> {
    const fixture = TestBed.createComponent(ClaimWorkbenchComponent);
    fixture.componentRef.setInput('taskId', 'task-c10');
    fixture.componentRef.setInput('token', 'approver-token');
    fixture.componentRef.setInput('role', role);
    fixture.componentRef.setInput('userId', 'approver-1');
    fixture.detectChanges();
    http.expectOne('/api/v1/claims/task-c10').flush(workbench(claimedBy));
    http.expectOne('/api/v1/documents/doc-c10/content').flush(new Blob(['%PDF-1.7']));
    fixture.detectChanges();
    return fixture;
  }

  it('explains the route and highlights the injury conflict', () => {
    const element = render(null).nativeElement as HTMLElement;

    expect(element.querySelector('#route-title')?.textContent).toContain('Bodily injury');
    expect(element.textContent).toContain('Injury in email, not on form');
    expect(element.textContent).toContain(
      'Needs review: No injury is indicated anywhere in the packet',
    );
    expect(element.querySelectorAll('.field.conflict')).toHaveLength(2);
    expect(element.querySelector('iframe')?.getAttribute('title')).toContain('page 3');
  });

  it('requires a claim before decisions and edits', () => {
    const element = render(null).nativeElement as HTMLElement;
    const approve = Array.from(element.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent.includes('Approve intake'),
    );

    expect(approve?.disabled).toBe(true);
    expect(element.textContent).toContain('Claim this task');
  });

  it('re-routes with a queue and reason', () => {
    const fixture = render('approver-1');
    const element = fixture.nativeElement as HTMLElement;
    const select = element.querySelector<HTMLSelectElement>('#reroute-queue');
    const reason = element.querySelector<HTMLTextAreaElement>('#reroute-reason');
    if (!select || !reason) throw new Error('re-route controls missing');
    select.value = 'DESK_ADJUSTER';
    select.dispatchEvent(new Event('change'));
    reason.value = 'Injury is minor; handle at the desk.';
    reason.dispatchEvent(new Event('input'));
    Array.from(element.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent.includes('Re-route claim'))
      ?.click();

    const request = http.expectOne('/api/v1/claims/task-c10/reroute');
    expect(request.request.body).toEqual({
      to_queue: 'DESK_ADJUSTER',
      reason: 'Injury is minor; handle at the desk.',
    });
    request.flush({
      review_queue: 'DESK_ADJUSTER',
      reason_code: 'REVIEWER_REROUTE',
      flags: [],
      routed_by: 'REVIEWER',
      routing_version: null,
    });
    http.expectOne('/api/v1/claims/task-c10').flush(workbench('approver-1'));
    fixture.detectChanges();
    expect(element.textContent).toContain('Re-routed to Desk adjuster.');
  });

  it('submits typed field corrections with the decision reason', () => {
    const fixture = render('approver-1');
    const element = fixture.nativeElement as HTMLElement;
    Array.from(element.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.getAttribute('aria-label') === 'Correct Grand total')
      ?.click();
    fixture.detectChanges();
    const input = element.querySelector<HTMLInputElement>(
      'input[aria-label="New value for Grand total"]',
    );
    if (!input) throw new Error('correction input missing');
    input.value = '2600.00';
    input.dispatchEvent(new Event('input'));
    const reason = element.querySelector<HTMLTextAreaElement>('#decision-reason');
    if (!reason) throw new Error('reason missing');
    reason.value = 'Shop supplement received.';
    reason.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    Array.from(element.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent.includes('Submit corrections (1)'))
      ?.click();

    const request = http.expectOne('/api/v1/review-tasks/task-c10/complete');
    expect(request.request.body).toEqual({
      decision: 'REQUEST_CORRECTION',
      reason: 'Shop supplement received.',
      field_corrections: [
        { field: 'estimate_total', new_value: '2600.00', reason: 'Shop supplement received.' },
      ],
    });
    request.flush({});
  });
});
