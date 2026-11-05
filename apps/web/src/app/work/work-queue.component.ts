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

import {
  PACKS,
  QUEUE_LABELS,
  SERVICE_LEVEL_LABELS,
  flagLabel,
  formatDate,
  formatMinutes,
  formatMoney,
} from './work-labels';
import {
  PackType,
  ReviewQueueItem,
  ReviewQueueView,
  ReviewApiService,
  ReviewQueue,
} from './review-api.service';

const REFRESH_MS = 60_000;

interface QueueTab {
  readonly key: ReviewQueue | 'ALL';
  readonly label: string;
  readonly count: number;
}

@Component({
  selector: 'app-work-queue',
  standalone: true,
  templateUrl: './work-queue.component.html',
  styleUrl: './work-queue.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorkQueueComponent implements OnInit {
  private readonly api = inject(ReviewApiService);
  private readonly destroyRef = inject(DestroyRef);

  readonly token = input('');
  readonly initialType = input<PackType>('CLAIM_PACKET');
  readonly openTask = output<string>();

  protected readonly packType = signal<PackType>('CLAIM_PACKET');
  protected readonly pack = computed(() => PACKS[this.packType()]);
  protected readonly packOptions = Object.values(PACKS);

  protected readonly view = signal<ReviewQueueView | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly active = signal<ReviewQueue | 'ALL'>('ALL');

  protected readonly tabs = computed<readonly QueueTab[]>(() => {
    const counts = this.view()?.counts_by_queue ?? {};
    const total = this.view()?.items.length ?? 0;
    return [
      { key: 'ALL', label: 'All', count: total },
      ...this.pack().queues.map((key) => ({
        key,
        label: QUEUE_LABELS[key],
        count: counts[key] ?? 0,
      })),
    ];
  });
  protected readonly rows = computed(() => {
    const items = this.view()?.items ?? [];
    const active = this.active();
    return active === 'ALL' ? items : items.filter((item) => item.route.review_queue === active);
  });
  protected readonly levels = computed(() => {
    const counts = this.view()?.counts_by_service_level ?? {};
    return {
      overdue: counts['OVERDUE'] ?? 0,
      dueSoon: counts['DUE_SOON'] ?? 0,
      onTrack: counts['ON_TRACK'] ?? 0,
    };
  });

  protected readonly queueLabel = (queue: ReviewQueue): string => QUEUE_LABELS[queue];
  protected readonly flag = flagLabel;
  protected readonly money = formatMoney;
  protected readonly date = formatDate;
  protected readonly duration = formatMinutes;

  ngOnInit(): void {
    this.packType.set(this.initialType());
    this.refresh();
    interval(REFRESH_MS)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.refresh();
      });
  }

  protected refresh(): void {
    this.loading.set(true);
    this.api.queue(this.packType(), this.token()).subscribe({
      next: (view) => {
        this.view.set(view);
        this.error.set(null);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Work queues are unavailable. Connect as a reviewer or approver.');
        this.loading.set(false);
      },
    });
  }

  protected choosePack(type: PackType): void {
    if (type === this.packType()) return;
    this.packType.set(type);
    this.active.set('ALL');
    this.view.set(null);
    this.refresh();
  }

  protected pick(key: ReviewQueue | 'ALL'): void {
    this.active.set(key);
  }

  protected levelLabel(item: ReviewQueueItem): string {
    return SERVICE_LEVEL_LABELS[item.service_level.status];
  }

  protected levelDetail(item: ReviewQueueItem): string {
    const level = item.service_level;
    const target = formatMinutes(level.target_minutes);
    if (level.status === 'OVERDUE')
      return `${formatMinutes(level.minutes_remaining)} past ${target}`;
    if (level.status === 'MET' || level.status === 'MISSED') return `Target ${target}`;
    return `${formatMinutes(level.minutes_remaining)} left of ${target}`;
  }

  protected levelClass(item: ReviewQueueItem): string {
    const status = item.service_level.status;
    if (status === 'OVERDUE' || status === 'MISSED') return 'claims-chip strong';
    if (status === 'DUE_SOON') return 'claims-chip attention';
    return 'claims-chip';
  }
}
