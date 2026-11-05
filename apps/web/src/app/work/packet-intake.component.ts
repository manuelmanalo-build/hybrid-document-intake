import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { interval } from 'rxjs';

import { DocumentDetail, IntakeApiService } from '../intake-api.service';

const POLL_MS = 4_000;
export const INTAKE_STEPS = [
  'Uploaded',
  'Classified',
  'Pages labeled',
  'Extracted',
  'Rules checked',
  'Ready for review',
] as const;

type StepState = 'done' | 'active' | 'waiting' | 'failed';
type FileKind = 'QUEUED' | 'PROCESSING' | 'READY' | 'PROBLEM';

interface IntakeFile {
  readonly key: string;
  readonly name: string;
  readonly size: number;
  readonly kind: FileKind;
  readonly progress: number;
  readonly documentId: string | null;
  readonly done: number;
  readonly result: string;
  readonly file: File | null;
  readonly duplicate: boolean;
}

@Component({
  selector: 'app-packet-intake',
  standalone: true,
  templateUrl: './packet-intake.component.html',
  styleUrl: './packet-intake.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PacketIntakeComponent implements OnInit {
  private readonly api = inject(IntakeApiService);
  private readonly destroyRef = inject(DestroyRef);
  private uploading = false;

  readonly token = input('');
  readonly canSubmit = input(false);
  readonly openQueues = output();

  protected readonly files = signal<readonly IntakeFile[]>([]);
  protected readonly filter = signal<FileKind | 'ALL'>('ALL');
  protected readonly steps = INTAKE_STEPS;
  protected readonly filters = computed(() => {
    const files = this.files();
    const count = (kind: FileKind | 'ALL'): number =>
      kind === 'ALL' ? files.length : files.filter((file) => file.kind === kind).length;
    return (
      [
        ['ALL', 'All'],
        ['PROCESSING', 'Processing'],
        ['READY', 'Ready for review'],
        ['PROBLEM', 'Upload problems'],
      ] as const
    ).map(([key, label]) => ({ key, label, count: count(key) }));
  });
  protected readonly visible = computed(() => {
    const active = this.filter();
    return active === 'ALL' ? this.files() : this.files().filter((file) => file.kind === active);
  });

  ngOnInit(): void {
    interval(POLL_MS)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.poll();
      });
  }

  protected choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    const chosen = Array.from(input.files ?? []);
    input.value = '';
    this.files.update((files) => [
      ...files,
      ...chosen.map((file) => ({
        key: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        kind: 'QUEUED' as const,
        progress: 0,
        documentId: null,
        done: 0,
        result: 'Waiting to upload',
        file,
        duplicate: false,
      })),
    ]);
    this.uploadNext();
  }

  protected stepState(file: IntakeFile, index: number): StepState {
    if (file.kind === 'PROBLEM') return index === 0 ? 'failed' : 'waiting';
    if (index < file.done) return 'done';
    if (index === file.done && file.kind !== 'READY') return 'active';
    return 'waiting';
  }

  protected progressLabel(file: IntakeFile): string {
    if (file.kind === 'PROBLEM') return 'Stopped at upload';
    return `${String(file.done)} of ${String(INTAKE_STEPS.length)} steps done`;
  }

  protected size(bytes: number): string {
    return bytes >= 1024 * 1024
      ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
      : `${(bytes / 1024).toFixed(1)} KB`;
  }

  private uploadNext(): void {
    if (this.uploading) return;
    const next = this.files().find((file) => file.kind === 'QUEUED' && file.file !== null);
    if (!next?.file) return;
    this.uploading = true;
    this.patch(next.key, { result: 'Uploading…' });
    this.api.upload(next.file, next.key, this.token()).subscribe({
      next: (event) => {
        if (event.type === HttpEventType.UploadProgress) {
          this.patch(next.key, {
            progress: event.total ? Math.round((event.loaded / event.total) * 100) : 0,
          });
        }
        if (event.type === HttpEventType.Response && event.body) {
          const body = event.body;
          this.patch(next.key, {
            kind: 'PROCESSING',
            documentId: body.document_id,
            done: 1,
            file: null,
            duplicate: body.duplicate_hash_detected,
            result: 'Classifying document',
          });
        }
      },
      error: (error: unknown) => {
        this.patch(next.key, { kind: 'PROBLEM', file: null, result: this.problem(error) });
        this.uploading = false;
        this.uploadNext();
      },
      complete: () => {
        this.uploading = false;
        this.uploadNext();
      },
    });
  }

  private poll(): void {
    for (const file of this.files()) {
      if (file.kind !== 'PROCESSING' || file.documentId === null) continue;
      this.api.document(file.documentId, this.token()).subscribe({
        next: (detail) => {
          this.patch(file.key, progressFrom(detail));
        },
      });
    }
  }

  private patch(key: string, changes: Partial<IntakeFile>): void {
    this.files.update((files) =>
      files.map((file) => (file.key === key ? { ...file, ...changes } : file)),
    );
  }

  private problem(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const detail = (error.error as { detail?: unknown } | null)?.detail;
      if (typeof detail === 'string') return `Rejected: ${detail}`;
      if (error.status === 401 || error.status === 403) return 'Rejected: connect as a submitter';
      if (error.status === 413) return 'Rejected: file is over the size limit';
    }
    return 'Rejected: upload failed';
  }
}

export function progressFrom(detail: DocumentDetail): Partial<IntakeFile> {
  const completed = new Set(
    detail.agent_tools
      .filter((tool) => tool['status'] === 'COMPLETED')
      .map((tool) => String(tool['tool'])),
  );
  const any = (prefix: string): boolean => [...completed].some((name) => name.startsWith(prefix));
  if (detail.document.graph_status === 'FAILED') {
    return { kind: 'PROBLEM', result: 'Processing failed; see audit trail' };
  }
  if (any('extract_invoice_')) {
    return { kind: 'READY', done: 6, result: 'Not a claim or brief: classified as an invoice' };
  }
  const reached = [
    true,
    completed.has('classify_document'),
    any('segment_'),
    [...completed].some(
      (name) => name.startsWith('extract_') && !name.startsWith('extract_invoice_'),
    ),
    detail.authoritative_validation.length > 0,
    detail.review !== null,
  ];
  const done = reached.findIndex((value) => !value);
  if (done === -1) return { kind: 'READY', done: 6, result: 'Ready for review' };
  const next = [
    '',
    'Classifying document',
    'Labeling pages',
    'Extracting fields',
    'Checking rules',
    'Routing to a queue',
  ];
  return { kind: 'PROCESSING', done, result: next[done] ?? 'Processing' };
}
