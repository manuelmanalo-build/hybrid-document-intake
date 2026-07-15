import { HttpEventType } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  computed,
  inject,
  signal,
} from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

import {
  DocumentCreated,
  DocumentDetail,
  DocumentSummary,
  FieldCorrection,
  IntakeApiService,
  ReviewDecision,
  ReviewTask,
  SessionIdentity,
} from './intake-api.service';
import { StatusBadgeComponent } from './status-badge.component';

interface EditableField {
  readonly name: string;
  readonly originalValue: string;
  readonly confidence: string;
  readonly pageNumber: number | null;
  readonly evidence: string;
  value: string;
  reason: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [StatusBadgeComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent implements OnDestroy {
  private readonly api = inject(IntakeApiService);
  private readonly sanitizer = inject(DomSanitizer);
  private pdfObjectUrl: string | null = null;

  protected readonly activeView = signal<'intake' | 'documents' | 'review'>('intake');
  protected readonly selectedFile = signal<File | null>(null);
  protected readonly idempotencyKey = signal(
    localStorage.getItem('document-intake-idempotency-key') ?? crypto.randomUUID(),
  );
  protected readonly accessToken = signal('');
  protected readonly identity = signal<SessionIdentity | null>(null);
  protected readonly submitting = signal(false);
  protected readonly uploadProgress = signal(0);
  protected readonly result = signal<DocumentCreated | null>(null);
  protected readonly documents = signal<readonly DocumentSummary[]>([]);
  protected readonly reviewTasks = signal<readonly ReviewTask[]>([]);
  protected readonly selectedDetail = signal<DocumentDetail | null>(null);
  protected readonly editableFields = signal<readonly EditableField[]>([]);
  protected readonly selectedTask = signal<ReviewTask | null>(null);
  protected readonly pdfUrl = signal<SafeResourceUrl | null>(null);
  protected readonly decisionReason = signal('');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly canReview = computed(() =>
    ['REVIEWER', 'APPROVER'].includes(this.identity()?.role ?? ''),
  );
  protected readonly canApprove = computed(() => this.identity()?.role === 'APPROVER');

  constructor() {
    localStorage.setItem('document-intake-idempotency-key', this.idempotencyKey());
  }

  ngOnDestroy(): void {
    if (this.pdfObjectUrl) URL.revokeObjectURL(this.pdfObjectUrl);
  }

  protected selectFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile.set(input.files?.item(0) ?? null);
    this.result.set(null);
    this.clearMessages();
  }

  protected setAccessToken(event: Event): void {
    this.accessToken.set((event.target as HTMLInputElement).value);
  }

  protected connect(): void {
    if (!this.accessToken()) return;
    this.busy.set(true);
    this.clearMessages();
    this.api.session(this.accessToken()).subscribe({
      next: (identity) => {
        this.identity.set(identity);
        this.busy.set(false);
        this.refreshDocuments();
        if (this.canReview()) this.refreshReviewQueue();
      },
      error: () => {
        this.fail('Authentication failed. Check the FastAPI demo credential.');
      },
    });
  }

  protected submit(): void {
    const file = this.selectedFile();
    if (file === null || !this.accessToken() || this.submitting()) return;
    this.submitting.set(true);
    this.uploadProgress.set(0);
    this.clearMessages();
    this.api.upload(file, this.idempotencyKey(), this.accessToken()).subscribe({
      next: (event) => {
        if (event.type === HttpEventType.UploadProgress && event.total) {
          this.uploadProgress.set(Math.round((100 * event.loaded) / event.total));
        }
        if (event.type === HttpEventType.Response && event.body) {
          this.result.set(event.body);
          this.uploadProgress.set(100);
          this.submitting.set(false);
          const nextKey = crypto.randomUUID();
          this.idempotencyKey.set(nextKey);
          localStorage.setItem('document-intake-idempotency-key', nextKey);
          this.notice.set(
            'Upload accepted. Processing was queued without calling LangGraph inline.',
          );
          this.refreshDocuments();
        }
      },
      error: () => {
        this.error.set('Upload failed. Retrying preserves the current idempotency key.');
        this.submitting.set(false);
      },
    });
  }

  protected navigate(view: 'intake' | 'documents' | 'review'): void {
    this.activeView.set(view);
    this.clearMessages();
    if (view === 'documents') this.refreshDocuments();
    if (view === 'review') this.refreshReviewQueue();
  }

  protected openDocument(id: string, task?: ReviewTask): void {
    this.busy.set(true);
    this.selectedTask.set(task ?? null);
    this.clearMessages();
    this.api.document(id, this.accessToken()).subscribe({
      next: (detail) => {
        this.selectedDetail.set(detail);
        this.editableFields.set(
          detail.extracted_fields.map((field) => ({
            name: field.name,
            originalValue: this.displayValue(field.value, ''),
            value: this.displayValue(field.value, ''),
            reason: '',
            confidence: field.confidence,
            pageNumber: field.page_number,
            evidence: field.evidence_excerpt,
          })),
        );
        this.loadPdf(id);
        this.busy.set(false);
      },
      error: () => {
        this.fail('Document detail could not be loaded.');
      },
    });
  }

  protected claim(task: ReviewTask): void {
    this.runTaskCommand(this.api.claim(task.id, this.accessToken()), 'Task claimed.');
  }

  protected unclaim(task: ReviewTask): void {
    this.runTaskCommand(this.api.unclaim(task.id, this.accessToken()), 'Task released.');
  }

  protected editValue(index: number, event: Event): void {
    this.updateField(index, { value: (event.target as HTMLInputElement).value });
  }

  protected editReason(index: number, event: Event): void {
    this.updateField(index, { reason: (event.target as HTMLInputElement).value });
  }

  protected setDecisionReason(event: Event): void {
    this.decisionReason.set((event.target as HTMLTextAreaElement).value);
  }

  protected complete(decision: ReviewDecision): void {
    const task = this.selectedTask();
    if (!task || !this.decisionReason().trim()) {
      this.error.set('Provide an overall decision reason before completing review.');
      return;
    }
    const corrections: FieldCorrection[] = [];
    for (const field of this.editableFields()) {
      if (field.value !== field.originalValue) {
        if (!field.reason.trim()) {
          this.error.set(`Provide a correction reason for ${this.fieldLabel(field.name)}.`);
          return;
        }
        corrections.push({
          field: field.name,
          new_value: field.value || null,
          reason: field.reason,
        });
      }
    }
    this.busy.set(true);
    this.clearMessages();
    this.api
      .complete(task.id, decision, this.decisionReason().trim(), corrections, this.accessToken())
      .subscribe({
        next: () => {
          this.notice.set(`${this.decisionLabel(decision)} recorded and queued for graph resume.`);
          this.busy.set(false);
          this.selectedDetail.set(null);
          this.selectedTask.set(null);
          this.refreshReviewQueue();
          this.refreshDocuments();
        },
        error: () => {
          this.fail('Review could not be completed. Refresh the task before retrying.');
        },
      });
  }

  protected fieldLabel(name: string): string {
    return name.replaceAll('_', ' ');
  }

  protected value(record: Record<string, unknown>, key: string): string {
    return this.displayValue(record[key], '—');
  }

  protected decisionLabel(value: string): string {
    return value.replaceAll('_', ' ').toLowerCase();
  }

  private refreshDocuments(): void {
    if (!this.accessToken()) return;
    this.api.documents(this.accessToken()).subscribe({
      next: (documents) => {
        this.documents.set(documents);
      },
      error: () => {
        this.error.set('Document list is unavailable.');
      },
    });
  }

  private refreshReviewQueue(): void {
    if (!this.accessToken() || !this.canReview()) return;
    this.api.reviewTasks(this.accessToken()).subscribe({
      next: (tasks) => {
        this.reviewTasks.set(tasks);
      },
      error: () => {
        this.error.set('Review queue is unavailable for this role.');
      },
    });
  }

  private loadPdf(documentId: string): void {
    this.api.pdf(documentId, this.accessToken()).subscribe({
      next: (blob) => {
        if (this.pdfObjectUrl) URL.revokeObjectURL(this.pdfObjectUrl);
        this.pdfObjectUrl = URL.createObjectURL(blob);
        // This local object URL comes only from the authenticated FastAPI Blob response.
        this.pdfUrl.set(this.sanitizer.bypassSecurityTrustResourceUrl(this.pdfObjectUrl));
      },
      error: () => {
        this.error.set('The PDF preview is unavailable.');
      },
    });
  }

  private runTaskCommand(request: ReturnType<IntakeApiService['claim']>, notice: string): void {
    this.busy.set(true);
    this.clearMessages();
    request.subscribe({
      next: (task) => {
        this.selectedTask.set(task);
        this.notice.set(notice);
        this.busy.set(false);
        this.refreshReviewQueue();
      },
      error: () => {
        this.fail('The task changed concurrently. Refresh and try again.');
      },
    });
  }

  private updateField(
    index: number,
    update: Partial<Pick<EditableField, 'value' | 'reason'>>,
  ): void {
    this.editableFields.update((fields) =>
      fields.map((field, fieldIndex) => (fieldIndex === index ? { ...field, ...update } : field)),
    );
  }

  private clearMessages(): void {
    this.error.set(null);
    this.notice.set(null);
  }

  private displayValue(value: unknown, empty: string): string {
    if (value === null || value === undefined) return empty;
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
      return value.toString();
    }
    try {
      return JSON.stringify(value);
    } catch {
      return empty;
    }
  }

  private fail(message: string): void {
    this.error.set(message);
    this.busy.set(false);
    this.submitting.set(false);
  }
}
