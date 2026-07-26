import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppComponent } from './app.component';

describe('AppComponent', () => {
  let http: HttpTestingController;

  function renderComponent(demoAuth = false): ComponentFixture<AppComponent> {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const options = http.expectOne('/api/v1/demo-auth/options');
    if (demoAuth) {
      options.flush({
        roles: ['SUBMITTER', 'REVIEWER', 'APPROVER', 'OPERATIONS_ADMIN', 'AUDITOR'],
        session_ttl_seconds: 3600,
      });
    } else {
      options.flush({}, { status: 404, statusText: 'Not Found' });
    }
    fixture.detectChanges();
    return fixture;
  }

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('renders a semantic, keyboard-addressable intake workspace', () => {
    const fixture = renderComponent();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('main#main-content')).not.toBeNull();
    expect(element.querySelector('h2')?.textContent).toContain('Submit an AP invoice');
    expect(element.querySelector('label[for="document"]')).not.toBeNull();
    expect(element.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(element.querySelector('.skip-link')?.textContent).toContain('Skip to main content');
    expect(element.querySelector('nav [aria-current="page"]')?.textContent).toContain('Upload');
  });

  it('preserves an idempotency key in local storage before upload', () => {
    const fixture = renderComponent();

    const key = localStorage.getItem('document-intake-idempotency-key');
    expect(key).toBeTruthy();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain(key);
  });

  it('shows review actions only after FastAPI returns an authorized role', () => {
    const fixture = renderComponent();
    const element = fixture.nativeElement as HTMLElement;
    const token = element.querySelector<HTMLInputElement>('#access-token');
    const connectButton = element.querySelector<HTMLButtonElement>('.session-row button');
    if (token === null || connectButton === null)
      throw new Error('Session controls were not found');
    token.value = 'reviewer-token';
    token.dispatchEvent(new Event('input'));
    connectButton.click();

    http.expectOne('/api/v1/session').flush({
      tenant_id: crypto.randomUUID(),
      user_id: crypto.randomUUID(),
      role: 'REVIEWER',
      subject: 'unit-reviewer',
    });
    http.expectOne('/api/v1/documents').flush([]);
    http.expectOne('/api/v1/review-tasks').flush([]);
    fixture.detectChanges();

    expect(element.textContent).toContain('Review queue');
  });

  it('connects a local demo role without exposing an access token and enables submitter upload', () => {
    const fixture = renderComponent(true);
    const element = fixture.nativeElement as HTMLElement;
    const role = element.querySelector<HTMLSelectElement>('#demo-role');
    const connectButton = element.querySelector<HTMLButtonElement>('.demo-session button');
    if (role === null || connectButton === null) throw new Error('Demo session controls not found');

    role.value = 'SUBMITTER';
    role.dispatchEvent(new Event('change'));
    connectButton.click();

    const connect = http.expectOne('/api/v1/demo-auth/session');
    expect(connect.request.body).toEqual({ role: 'SUBMITTER' });
    expect(connect.request.withCredentials).toBe(true);
    connect.flush({
      tenant_id: crypto.randomUUID(),
      user_id: crypto.randomUUID(),
      role: 'SUBMITTER',
      subject: 'local-demo-submitter',
    });
    http.expectOne('/api/v1/documents').flush([]);
    fixture.detectChanges();

    const fileInput = element.querySelector<HTMLInputElement>('#document');
    const upload = element.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (fileInput === null || upload === null) throw new Error('Upload controls not found');
    const selected = new File(['%PDF-1.7'], 'invoice.pdf', { type: 'application/pdf' });
    Object.defineProperty(fileInput, 'files', {
      configurable: true,
      value: { item: () => selected, length: 1 },
    });
    fileInput.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    expect(element.querySelector('#access-token')).toBeNull();
    expect(upload.disabled).toBe(false);
    expect(element.textContent).toContain('SUBMITTER');
  });
});
