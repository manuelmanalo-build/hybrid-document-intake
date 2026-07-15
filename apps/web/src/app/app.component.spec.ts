import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppComponent } from './app.component';

describe('AppComponent', () => {
  let http: HttpTestingController;

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
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('main#main-content')).not.toBeNull();
    expect(element.querySelector('h2')?.textContent).toContain('Submit an AP invoice');
    expect(element.querySelector('label[for="document"]')).not.toBeNull();
    expect(element.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(element.querySelector('.skip-link')?.textContent).toContain('Skip to main content');
    expect(element.querySelector('nav [aria-current="page"]')?.textContent).toContain('Upload');
  });

  it('preserves an idempotency key in local storage before upload', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();

    const key = localStorage.getItem('document-intake-idempotency-key');
    expect(key).toBeTruthy();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain(key);
  });

  it('shows review actions only after FastAPI returns an authorized role', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
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
});
