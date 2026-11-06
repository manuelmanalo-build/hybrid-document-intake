import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { WorkbenchComponent } from './workbench.component';
import { briefWorkbench, cdpWorkbench, workbench } from './work-fixtures.spec-helpers';

describe('WorkbenchComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WorkbenchComponent],
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
  ): ComponentFixture<WorkbenchComponent> {
    const fixture = TestBed.createComponent(WorkbenchComponent);
    fixture.componentRef.setInput('taskId', 'task-c10');
    fixture.componentRef.setInput('token', 'approver-token');
    fixture.componentRef.setInput('role', role);
    fixture.componentRef.setInput('userId', 'approver-1');
    fixture.detectChanges();
    http.expectOne('/api/v1/review-queues/tasks/task-c10').flush(workbench(claimedBy));
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

    const request = http.expectOne('/api/v1/review-queues/tasks/task-c10/reroute');
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
    http.expectOne('/api/v1/review-queues/tasks/task-c10').flush(workbench('approver-1'));
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

  it('shows a brief with its deliverables and the email that moves the launch', () => {
    const fixture = TestBed.createComponent(WorkbenchComponent);
    fixture.componentRef.setInput('taskId', 'task-b12');
    fixture.componentRef.setInput('role', 'REVIEWER');
    fixture.componentRef.setInput('userId', 'reviewer-1');
    fixture.detectChanges();
    http.expectOne('/api/v1/review-queues/tasks/task-b12').flush(briefWorkbench());
    http
      .expectOne('/api/v1/review-queues/tasks/task-b12/client-questions')
      .flush({ draft: { subject: '', body: '', questions: [], reason_codes: [] }, sent: [] });
    http.expectOne('/api/v1/documents/doc-b12/content').flush(new Blob(['%PDF-1.7']));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('#route-title')?.textContent).toContain('Rush review');
    expect(element.textContent).toContain('Lead time at risk');
    expect(element.textContent).toContain('Video :15');
    expect(element.textContent).toContain('Routine posts');
    expect(element.querySelectorAll('.field.conflict')).toHaveLength(2);
    expect(element.querySelector('iframe')?.getAttribute('title')).toContain('page 2');
    const reroute = element.querySelector<HTMLSelectElement>('#reroute-queue');
    const options = Array.from(reroute?.options ?? []).map((option) => option.value);
    expect(options).toContain('TRAFFIC_REVIEW');
    expect(options).not.toContain('FAST_TRACK');
  });

  it('drafts client questions for a brief and records them only once claimed', () => {
    const fixture = TestBed.createComponent(WorkbenchComponent);
    fixture.componentRef.setInput('taskId', 'task-b12');
    fixture.componentRef.setInput('role', 'REVIEWER');
    fixture.componentRef.setInput('userId', 'reviewer-1');
    fixture.detectChanges();
    http.expectOne('/api/v1/review-queues/tasks/task-b12').flush(briefWorkbench('reviewer-1'));
    http.expectOne('/api/v1/review-queues/tasks/task-b12/client-questions').flush({
      draft: {
        subject: 'Questions on the Cold Brew New Year brief',
        body: 'Hi Elena,\n\n1. Could you confirm the final launch date?',
        questions: ['Could you confirm the final launch date?'],
        reason_codes: ['CORRESPONDENCE_CONFLICT'],
      },
      sent: [],
    });
    http.expectOne('/api/v1/documents/doc-b12/content').flush(new Blob(['%PDF-1.7']));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector<HTMLInputElement>('#question-subject')?.value).toBe(
      'Questions on the Cold Brew New Year brief',
    );
    expect(element.textContent).toContain('The system never contacts the client.');
    Array.from(element.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent.includes('Record as sent'))
      ?.click();

    const request = http.expectOne('/api/v1/review-queues/tasks/task-b12/client-questions/sent');
    expect(request.request.body).toEqual({
      subject: 'Questions on the Cold Brew New Year brief',
      body: 'Hi Elena,\n\n1. Could you confirm the final launch date?',
    });
  });

  it('shows scanner findings and checks every mapping for an onboarding request', () => {
    const fixture = TestBed.createComponent(WorkbenchComponent);
    fixture.componentRef.setInput('taskId', 'task-d11');
    fixture.componentRef.setInput('role', 'APPROVER');
    fixture.componentRef.setInput('userId', 'approver-1');
    fixture.detectChanges();
    http.expectOne('/api/v1/review-queues/tasks/task-d11').flush(cdpWorkbench('approver-1'));
    http.expectOne('/api/v1/documents/doc-d11/content').flush(new Blob(['%PDF-1.7']));
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('#route-title')?.textContent).toContain('Privacy review');
    const scan = element.querySelector('.scan');
    expect(scan?.classList).toContain('flagged');
    expect(scan?.textContent).toContain('Sample extract: 6 email addresses, 6 phone numbers');
    expect(scan?.textContent).toContain('Business contact details');
    const rows = Array.from(element.querySelectorAll('.mappings tbody tr'));
    expect(rows).toHaveLength(3);
    expect(rows[1]?.classList).toContain('undeclared');
    expect(rows[1]?.textContent).toContain('Not marked personal');
    expect(rows[2]?.textContent).toContain('Type mismatch');

    const select = element.querySelector<HTMLSelectElement>(
      'select[aria-label="Correct the mapping for signup"]',
    );
    if (!select) throw new Error('mapping select missing');
    select.value = '__exclude__';
    select.dispatchEvent(new Event('change'));
    const reason = element.querySelector<HTMLTextAreaElement>('#decision-reason');
    if (!reason) throw new Error('reason missing');
    reason.value = 'Client will send signup dates separately.';
    reason.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    Array.from(element.querySelectorAll<HTMLButtonElement>('button'))
      .find((button) => button.textContent.includes('Submit corrections (1)'))
      ?.click();

    const request = http.expectOne('/api/v1/review-tasks/task-d11/complete');
    expect(request.request.body).toEqual({
      decision: 'REQUEST_CORRECTION',
      reason: 'Client will send signup dates separately.',
      field_corrections: [
        {
          field: 'mappings[2].excluded',
          new_value: 'true',
          reason: 'Client will send signup dates separately.',
        },
      ],
    });
  });
});
