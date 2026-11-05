import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { Observable } from 'rxjs';

import { FieldCorrection, IntakeApiService, ReviewDecision, UserRole } from '../intake-api.service';
import {
  ASSET_LABELS,
  FieldKind,
  PACKS,
  QUEUE_LABELS,
  SEGMENT_LABELS,
  flagLabel,
  formatDate,
  formatMoney,
} from './work-labels';
import { ReviewApiService, ReviewQueue, WorkField, Workbench } from './review-api.service';

interface FieldRow {
  readonly name: string;
  readonly label: string;
  readonly display: string;
  readonly confidence: string | null;
  readonly page: number | null;
  readonly evidence: string | null;
  readonly reviewed: boolean;
  readonly conflict: boolean;
  readonly kind: FieldKind;
  readonly items: readonly ListItemRow[];
}

interface ListItemRow {
  readonly key: string;
  readonly asset: string;
  readonly quantity: string;
  readonly description: string;
  readonly due: string;
  readonly page: number | null;
  readonly evidence: string | null;
}

interface FieldGroup {
  readonly title: string;
  readonly rows: readonly FieldRow[];
}

@Component({
  selector: 'app-workbench',
  standalone: true,
  templateUrl: './workbench.component.html',
  styleUrl: './workbench.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorkbenchComponent implements OnInit, OnDestroy {
  private readonly claims = inject(ReviewApiService);
  private readonly intake = inject(IntakeApiService);
  private readonly sanitizer = inject(DomSanitizer);
  private objectUrl: string | null = null;

  readonly taskId = input.required<string>();
  readonly token = input('');
  readonly role = input<UserRole | null>(null);
  readonly userId = input<string | null>(null);
  readonly back = output();
  readonly completed = output<string>();

  protected readonly bench = signal<Workbench | null>(null);
  protected readonly page = signal(1);
  protected readonly pdfUrl = signal<SafeResourceUrl | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly editing = signal<string | null>(null);
  protected readonly drafts = signal<Readonly<Record<string, string>>>({});
  protected readonly decisionReason = signal('');
  protected readonly rerouteQueue = signal<ReviewQueue | ''>('');
  protected readonly rerouteReason = signal('');

  protected readonly item = computed(() => this.bench()?.item ?? null);
  protected readonly pack = computed(() => PACKS[this.bench()?.document_type ?? 'CLAIM_PACKET']);
  protected readonly queueLabel = computed(() => {
    const current = this.item();
    return current ? QUEUE_LABELS[current.route.review_queue] : '';
  });
  protected readonly flags = computed(() =>
    (this.item()?.route.flags ?? []).map((code) => flagLabel(code)),
  );
  protected readonly attentionRules = computed(() =>
    (this.bench()?.rules ?? []).filter(
      (rule) => rule.outcome === 'FAIL' || rule.outcome === 'REVIEW',
    ),
  );
  protected readonly passedRules = computed(() =>
    (this.bench()?.rules ?? []).filter((rule) => rule.outcome === 'PASS'),
  );
  protected readonly pages = computed(() =>
    (this.bench()?.segments ?? []).map((segment) => ({
      number: segment.page_number,
      label: SEGMENT_LABELS[segment.label] ?? segment.label,
    })),
  );
  protected readonly groups = computed<readonly FieldGroup[]>(() => {
    const pack = this.pack();
    const fields = new Map((this.bench()?.fields ?? []).map((field) => [field.name, field]));
    const conflicts = new Set(pack.conflicts((name) => fields.get(name)?.value ?? null));
    return pack.groups
      .map(([title, names]) => ({
        title,
        rows: names
          .map((name) => fields.get(name))
          .filter((field): field is WorkField => field !== undefined)
          .map((field) => this.row(field, conflicts.has(field.name))),
      }))
      .filter((group) => group.rows.length > 0);
  });
  protected readonly corrections = computed<readonly FieldCorrection[]>(() =>
    Object.entries(this.drafts()).map(([field, newValue]) => ({
      field,
      new_value: newValue.trim() === '' ? null : newValue.trim(),
      reason: this.decisionReason().trim() || 'Reviewer correction',
    })),
  );
  protected readonly claimedByMe = computed(() => {
    const current = this.item();
    return (
      current !== null &&
      current.claimed_by_user_id !== null &&
      current.claimed_by_user_id === this.userId()
    );
  });
  protected readonly unclaimed = computed(() => this.item()?.claimed_by_user_id === null);
  protected readonly canReview = computed(() =>
    ['REVIEWER', 'APPROVER'].includes(this.role() ?? ''),
  );
  protected readonly canApprove = computed(() => this.role() === 'APPROVER');
  protected readonly rerouteTargets = computed(() =>
    this.pack()
      .queues.filter((queue) => queue !== this.item()?.route.review_queue)
      .map((queue) => ({
        value: queue,
        label: QUEUE_LABELS[queue],
      })),
  );

  ngOnInit(): void {
    this.load(true);
  }

  ngOnDestroy(): void {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
  }

  protected showPage(page: number | null): void {
    if (page === null) return;
    this.page.set(page);
    this.updatePdfUrl();
  }

  protected startEdit(row: FieldRow): void {
    this.editing.set(row.name);
    if (!(row.name in this.drafts())) {
      const field = this.bench()?.fields.find((candidate) => candidate.name === row.name);
      const raw = field?.value;
      this.drafts.update((drafts) => ({
        ...drafts,
        [row.name]: raw === null || raw === undefined ? '' : scalarText(raw),
      }));
    }
  }

  protected setDraft(name: string, event: Event): void {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    this.drafts.update((drafts) => ({ ...drafts, [name]: target.value }));
  }

  protected discard(name: string): void {
    this.drafts.update((drafts) =>
      Object.fromEntries(Object.entries(drafts).filter(([key]) => key !== name)),
    );
    if (this.editing() === name) this.editing.set(null);
  }

  protected draftFor(name: string): string {
    return this.drafts()[name] ?? '';
  }

  protected setText(target: 'decision' | 'reroute', event: Event): void {
    const value = (event.target as HTMLInputElement | HTMLTextAreaElement).value;
    if (target === 'decision') this.decisionReason.set(value);
    else this.rerouteReason.set(value);
  }

  protected setRerouteQueue(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.rerouteQueue.set(
      this.pack().queues.includes(value as ReviewQueue) ? (value as ReviewQueue) : '',
    );
  }

  protected takeTask(): void {
    this.run(this.intake.claim(this.taskId(), this.token()), 'Claim assigned to you.');
  }

  protected release(): void {
    this.run(this.intake.unclaim(this.taskId(), this.token()), 'Claim released back to the queue.');
  }

  protected reroute(): void {
    const queue = this.rerouteQueue();
    const reason = this.rerouteReason().trim();
    if (queue === '' || reason === '') {
      this.error.set('Choose a queue and give a reason to re-route.');
      return;
    }
    this.run(
      this.claims.reroute(this.taskId(), queue, reason, this.token()),
      `Re-routed to ${QUEUE_LABELS[queue]}.`,
      () => {
        this.rerouteQueue.set('');
        this.rerouteReason.set('');
      },
    );
  }

  protected decide(decision: ReviewDecision): void {
    const reason = this.decisionReason().trim();
    if (reason === '') {
      this.error.set('Give a reason for the decision.');
      return;
    }
    if (decision === 'REQUEST_CORRECTION' && this.corrections().length === 0) {
      this.error.set('Edit at least one field before requesting a correction.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.intake
      .complete(
        this.taskId(),
        decision,
        reason,
        decision === 'REQUEST_CORRECTION' ? this.corrections() : [],
        this.token(),
      )
      .subscribe({
        next: () => {
          this.busy.set(false);
          this.completed.emit(
            decision === 'APPROVE'
              ? `Approved the ${this.pack().singular}.`
              : decision === 'REJECT'
                ? `Rejected the ${this.pack().singular}.`
                : `Corrections submitted; the ${this.pack().singular} is being re-checked.`,
          );
        },
        error: () => {
          this.busy.set(false);
          this.error.set(
            'The decision was not accepted. Claim the task first and check the inputs.',
          );
        },
      });
  }

  private run(request: Observable<unknown>, message: string, after?: () => void): void {
    this.busy.set(true);
    this.error.set(null);
    request.subscribe({
      next: () => {
        after?.();
        this.notice.set(message);
        this.load(false);
      },
      error: () => {
        this.busy.set(false);
        this.error.set('That action was not accepted. Refresh and try again.');
      },
    });
  }

  private load(withPdf: boolean): void {
    this.busy.set(true);
    this.claims.workbench(this.taskId(), this.token()).subscribe({
      next: (bench) => {
        this.bench.set(bench);
        this.busy.set(false);
        if (withPdf) {
          const focus = PACKS[bench.document_type].focusField;
          const conflictPage = bench.fields.find(
            (field) => field.name === focus && field.value !== null && field.value !== false,
          )?.source_page;
          this.page.set(conflictPage ?? 1);
          this.loadPdf(bench.item.document_id);
        }
      },
      error: () => {
        this.busy.set(false);
        this.error.set('This task could not be loaded.');
      },
    });
  }

  private loadPdf(documentId: string): void {
    this.intake.pdf(documentId, this.token()).subscribe({
      next: (blob) => {
        if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
        this.objectUrl = URL.createObjectURL(blob);
        this.updatePdfUrl();
      },
      error: () => {
        this.error.set('The packet PDF could not be loaded.');
      },
    });
  }

  private updatePdfUrl(): void {
    if (!this.objectUrl) return;
    // A same-origin blob URL created above from the authorized PDF response; never user input.
    this.pdfUrl.set(
      this.sanitizer.bypassSecurityTrustResourceUrl(
        `${this.objectUrl}#page=${String(this.page())}`,
      ),
    );
  }

  private row(field: WorkField, conflict: boolean): FieldRow {
    const pack = this.pack();
    const kind = pack.kinds[field.name] ?? 'text';
    const values = Array.isArray(field.value) ? (field.value as readonly unknown[]) : [];
    return {
      name: field.name,
      label: pack.labels[field.name] ?? field.name,
      display: kind === 'list' ? `${String(values.length)} items` : this.display(field, kind),
      confidence:
        field.confidence === null ? null : `${String(Math.round(Number(field.confidence) * 100))}%`,
      page: field.source_page,
      evidence: field.evidence_excerpt,
      reviewed: field.reviewed,
      conflict,
      kind,
      items: values.map((value, index) => {
        const item = (value ?? {}) as Readonly<Record<string, unknown>>;
        const evidence = field.items?.[index];
        const asset = scalarText(item['asset_type']);
        return {
          key: `${field.name}-${String(index)}`,
          asset: ASSET_LABELS[asset] ?? asset,
          quantity: scalarText(item['quantity']),
          description: scalarText(item['description']),
          due: formatDate(item['due_date'] === null ? null : scalarText(item['due_date'])),
          page: evidence?.source_page ?? null,
          evidence: evidence?.evidence_excerpt ?? null,
        };
      }),
    };
  }

  private display(field: WorkField, kind: FieldKind): string {
    const value = field.value;
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (kind === 'money') return formatMoney(scalarText(value));
    if (kind === 'date') return formatDate(scalarText(value));
    if (kind === 'coverage') return value === 'COLLISION' ? 'Collision' : 'Comprehensive';
    return scalarText(value);
  }
}

function scalarText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return value === null || value === undefined ? '' : JSON.stringify(value);
}
