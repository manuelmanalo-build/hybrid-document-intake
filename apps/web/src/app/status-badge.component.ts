import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'app-status-badge',
  standalone: true,
  template: '<span class="badge" [attr.data-status]="status()">● {{ status() }}</span>',
  styles: `
    .badge {
      display: inline-flex;
      gap: 0.4rem;
      align-items: center;
      border: 1px solid #9aa7b8;
      border-radius: 999px;
      padding: 0.2rem 0.6rem;
      font-size: 0.8rem;
      font-weight: 750;
    }
    [data-status='COMPLETED'],
    [data-status='APPROVED'],
    [data-status='DELIVERED'] {
      background: #e4f4e9;
      color: #155c34;
      border-color: #74af88;
    }
    [data-status='FAILED'],
    [data-status='REJECTED'],
    [data-status='DEAD_LETTER'] {
      background: #fde9e7;
      color: #861f19;
      border-color: #d78c87;
    }
    [data-status='INTERRUPTED'],
    [data-status='OPEN'],
    [data-status='REVIEW'] {
      background: #fff2d6;
      color: #6e4700;
      border-color: #d6ad59;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StatusBadgeComponent {
  readonly status = input.required<string>();
}
