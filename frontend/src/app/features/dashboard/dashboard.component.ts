import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-dashboard',
  imports: [CommonModule, RouterLink, FormsModule],
  templateUrl: './dashboard.component.html',
})
export class DashboardComponent implements OnInit {
  strategies: any[] = [];
  loading = true;
  error = '';

  copyModalStrategy: any = null;
  copyNewName = '';
  copyError = '';

  constructor(private api: ApiService) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading = true;
    this.api.listStrategies().subscribe({
      next: (data) => { this.strategies = data; this.loading = false; },
      error: () => { this.error = 'Could not load strategies. Is the backend running?'; this.loading = false; }
    });
  }

  toggleEnabled(s: any): void {
    const newVal = !s.enabled;
    this.api.toggleStrategy(s.name, newVal).subscribe({
      next: (updated) => { s.enabled = updated.enabled; },
      error: () => alert('Failed to toggle strategy.')
    });
  }

  toggleMode(s: any): void {
    const newMode = s.mode === 'live' ? 'paper' : 'live';
    this.api.setStrategyMode(s.name, newMode).subscribe({
      next: (updated) => { s.mode = updated.mode; },
      error: () => alert('Failed to change mode.')
    });
  }

  deleteStrategy(s: any): void {
    if (!confirm(`Delete strategy "${s.name}"?`)) return;
    this.api.deleteStrategy(s.name).subscribe({
      next: () => this.strategies = this.strategies.filter(x => x.name !== s.name),
      error: () => alert('Failed to delete strategy.')
    });
  }

  openCopyModal(s: any): void {
    this.copyModalStrategy = s;
    this.copyNewName = s.name + '_copy';
    this.copyError = '';
  }

  closeCopyModal(): void { this.copyModalStrategy = null; }

  confirmCopy(): void {
    if (!this.copyNewName.trim()) { this.copyError = 'Name is required.'; return; }
    this.api.copyStrategy(this.copyModalStrategy.name, this.copyNewName.trim()).subscribe({
      next: (rec) => { this.strategies.push(rec); this.closeCopyModal(); },
      error: (e) => { this.copyError = e?.error?.detail || 'Copy failed.'; }
    });
  }

  categoryClass(cat: string): string {
    switch (cat) {
      case 'equity':  return 'bg-primary';
      case 'futures': return 'bg-warning text-dark';
      case 'options': return 'bg-info text-dark';
      default:        return 'bg-secondary';
    }
  }
}
