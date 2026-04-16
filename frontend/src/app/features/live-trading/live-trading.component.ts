import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-live-trading',
  imports: [CommonModule, FormsModule],
  templateUrl: './live-trading.component.html',
})
export class LiveTradingComponent implements OnInit, OnDestroy {
  positions: any = { live: [], paper: [], live_pnl: 0, paper_pnl: 0 };
  tradingStatus: any = { running: false, active_strategies: [] };
  activeTab: 'live' | 'paper' = 'paper';

  // Start modal
  showModal = false;
  strategies: any[] = [];
  modalForm = {
    strategy: '',
    symbol: 'NIFTY',
    exchange: 'NSE',
    interval: 'ONE_DAY',
    paper: true
  };
  exchanges = ['NSE', 'BSE', 'NFO', 'MCX'];
  intervals = ['ONE_MINUTE','THREE_MINUTE','FIVE_MINUTE','TEN_MINUTE','FIFTEEN_MINUTE',
               'THIRTY_MINUTE','ONE_HOUR','ONE_DAY'];

  loading = false;
  error = '';
  private pollTimer: any;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.loadAll();
    this.pollTimer = setInterval(() => this.loadPositions(), 5000);
  }

  ngOnDestroy(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
  }

  loadAll(): void {
    this.loadPositions();
    this.api.listStrategies().subscribe({ next: (s) => this.strategies = s });
    this.api.tradingStatus().subscribe({ next: (s) => this.tradingStatus = s });
  }

  loadPositions(): void {
    this.api.getPositions().subscribe({
      next: (p) => this.positions = p,
      error: () => {}
    });
    this.api.tradingStatus().subscribe({ next: (s) => this.tradingStatus = s });
  }

  startTrading(): void {
    if (!this.modalForm.strategy) { this.error = 'Select a strategy.'; return; }
    this.loading = true;
    this.error = '';
    this.api.startTrading(this.modalForm).subscribe({
      next: () => { this.showModal = false; this.loading = false; this.loadAll(); },
      error: (e) => { this.error = e?.error?.detail || 'Failed to start.'; this.loading = false; }
    });
  }

  stopTrading(): void {
    if (!confirm('Stop all running strategies?')) return;
    this.api.stopTrading().subscribe({ next: () => this.loadAll() });
  }

  exit(symbol: string): void {
    if (!confirm(`Exit position: ${symbol}?`)) return;
    this.api.exitPosition(symbol).subscribe({ next: () => this.loadPositions() });
  }

  exitAll(): void {
    if (!confirm('Exit ALL positions?')) return;
    this.api.exitAllPositions().subscribe({ next: () => this.loadPositions() });
  }

  pnlClass(pnl: number): string { return pnl >= 0 ? 'pnl-positive' : 'pnl-negative'; }

  currentPositions(): any[] {
    return this.activeTab === 'live' ? this.positions.live : this.positions.paper;
  }

  currentPnl(): number {
    return this.activeTab === 'live' ? this.positions.live_pnl : this.positions.paper_pnl;
  }
}
