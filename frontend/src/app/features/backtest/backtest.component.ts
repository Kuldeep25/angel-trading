import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { NgChartsModule } from 'ng2-charts';
import { ChartConfiguration } from 'chart.js';
import { Chart, registerables } from 'chart.js';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged, switchMap } from 'rxjs/operators';

Chart.register(...registerables);

@Component({
  selector: 'app-backtest',
  imports: [CommonModule, FormsModule, NgChartsModule],
  templateUrl: './backtest.component.html',
})
export class BacktestComponent implements OnInit, OnDestroy {
  // Form
  strategyName = '';
  symbol = 'RELIANCE';
  exchange = 'NSE';
  interval = 'FIVE_MINUTE';
  fromDate = '';
  toDate = '';
  capital = 100000;
  slPct     = 0;   // 0 → use strategy default
  tslPct    = 0;   // 0 → disabled
  targetPct = 0;   // 0 → disabled

  // Strategy-level defaults (from py class attributes)
  defaultSlPct:     number | null = null;
  defaultTslPct:    number | null = null;
  defaultTargetPct: number | null = null;

  instrumentType: 'equity' | 'futures' | 'options' = 'equity';
  instrumentTypes = [
    { value: 'equity',  label: 'Equity' },
    { value: 'futures', label: 'Futures' },
    { value: 'options', label: 'Options' },
  ];

  // Symbol typeahead
  symbolQuery = 'RELIANCE';
  symbolResults: any[] = [];
  showDropdown = false;
  symbolLoading = false;
  private searchSubject = new Subject<{q: string; type: string}>();
  private searchSub?: Subscription;

  strategies: any[] = [];
  exchanges = ['NSE', 'BSE', 'NFO', 'MCX'];
  intervals = ['ONE_MINUTE','THREE_MINUTE','FIVE_MINUTE','TEN_MINUTE','FIFTEEN_MINUTE',
               'THIRTY_MINUTE','ONE_HOUR','ONE_DAY'];

  // Results
  running = false;
  reconnecting = false;
  isConnectionError = false;
  error = '';
  result: any = null;

  // Chart
  chartData: ChartConfiguration<'line'>['data'] = { labels: [], datasets: [] };
  chartOptions: ChartConfiguration<'line'>['options'] = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { mode: 'index', intersect: false }
    },
    scales: {
      x: { ticks: { maxTicksLimit: 10, color: '#8b949e' }, grid: { color: '#30363d' } },
      y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } }
    }
  };

  constructor(private api: ApiService, private route: ActivatedRoute) {
    const today = new Date();
    const threeMonthsAgo = new Date();
    threeMonthsAgo.setMonth(today.getMonth() - 3);
    this.toDate = this.fmt(today);
    this.fromDate = this.fmt(threeMonthsAgo);
  }

  ngOnInit(): void {
    this.api.listStrategies().subscribe({ next: (s) => this.strategies = s });
    const sn = this.route.snapshot.queryParamMap.get('strategy');
    if (sn) { this.strategyName = sn; this.onStrategyChange(); }

    // Wire debounced symbol search
    this.searchSub = this.searchSubject.pipe(
      debounceTime(250),
      distinctUntilChanged((a, b) => a.q === b.q && a.type === b.type),
      switchMap(({q, type}) => {
        this.symbolLoading = true;
        return this.api.searchSymbols(q, type);
      })
    ).subscribe({
      next: (results) => { this.symbolResults = results; this.symbolLoading = false; this.showDropdown = true; },
      error: () => { this.symbolLoading = false; }
    });
  }

  ngOnDestroy(): void {
    this.searchSub?.unsubscribe();
  }

  onSymbolInput(): void {
    if (this.symbolQuery.length >= 1) {
      this.searchSubject.next({q: this.symbolQuery, type: this.instrumentType});
    } else {
      this.symbolResults = [];
      this.showDropdown = false;
    }
  }

  selectSymbol(item: any): void {
    // Always use the underlying name — for futures/options the backend resolves contracts
    this.symbol = item.name || item.raw_symbol || item.symbol;
    this.symbolQuery = this.symbol;
    this.showDropdown = false;
    // Auto-set exchange from search result (NFO for NIFTY, BFO for SENSEX, MCX for Gold, etc.)
    if (this.instrumentType !== 'equity' && item.exchange) {
      this.exchange = item.exchange;
    }
  }

  hideDropdown(): void {
    setTimeout(() => {
      this.showDropdown = false;
      // Sync symbol from whatever the user typed (uppercased)
      if (this.symbolQuery.trim()) {
        this.symbol = this.symbolQuery.trim().toUpperCase();
      }
    }, 200);
  }

  fmt(d: Date): string { return d.toISOString().split('T')[0]; }

  onStrategyChange(): void {
    if (!this.strategyName) {
      this.defaultSlPct = this.defaultTslPct = this.defaultTargetPct = null;
      this.slPct = this.tslPct = this.targetPct = 0;
      return;
    }
    this.api.getStrategy(this.strategyName).subscribe({
      next: (rec) => {
        this.defaultSlPct     = rec.default_sl_pct     ?? null;
        this.defaultTslPct    = rec.default_tsl_pct    ?? null;
        this.defaultTargetPct = rec.default_target_pct ?? null;
        // Always reset form to strategy defaults when strategy changes
        this.slPct     = this.defaultSlPct     ?? 0;
        this.tslPct    = this.defaultTslPct    ?? 0;
        this.targetPct = this.defaultTargetPct ?? 0;
      },
      error: () => { /* ignore */ }
    });
  }

  onInstrumentTypeChange(): void {
    if (this.instrumentType === 'equity') {
      this.exchange = 'NSE';
    } else if (!['NFO', 'BFO', 'MCX'].includes(this.exchange)) {
      // Only reset to NFO if we're not already on a valid derivative exchange
      this.exchange = 'NFO';
    }
    // Re-search with new instrument type
    this.symbolResults = [];
    this.showDropdown = false;
    if (this.symbolQuery.length >= 1) {
      this.searchSubject.next({q: this.symbolQuery, type: this.instrumentType});
    }
  }

  run(): void {
    if (!this.strategyName) { this.error = 'Select a strategy.'; return; }
    this.error = '';
    this.result = null;
    this.running = true;
    // Form value > 0: user override. Form value = 0: fall back to strategy default. 0 = disabled.
    const effectiveSl     = this.slPct     > 0 ? this.slPct     : (this.defaultSlPct     ?? 0);
    const effectiveTsl    = this.tslPct    > 0 ? this.tslPct    : (this.defaultTslPct    ?? 0);
    const effectiveTarget = this.targetPct > 0 ? this.targetPct : (this.defaultTargetPct ?? 0);
    const payload = {
      strategy_name: this.strategyName,
      symbol: this.symbol.toUpperCase(),
      exchange: this.exchange,
      instrument_type: this.instrumentType,
      interval: this.interval,
      from_date: this.fromDate,
      to_date: this.toDate,
      capital: this.capital,
      sl_pct:     effectiveSl,
      tsl_pct:    effectiveTsl,
      target_pct: effectiveTarget
    };
    this.api.runBacktest(payload).subscribe({
      next: (res) => {
        this.result = res;
        this.isConnectionError = false;
        this.buildChart(res.equity_curve);
        this.running = false;
      },
      error: (e) => {
        this.error = e?.error?.detail || 'Backtest failed.';
        this.isConnectionError = e?.status === 503;
        this.running = false;
      }
    });
  }

  reconnect(): void {
    this.reconnecting = true;
    this.error = '';
    this.api.reconnect().subscribe({
      next: (res) => {
        this.reconnecting = false;
        if (res?.status === 'ok') {
          this.error = '';
          this.isConnectionError = false;
        } else {
          this.error = 'Reconnect failed: ' + (res?.detail ?? 'unknown error');
        }
      },
      error: () => {
        this.reconnecting = false;
        this.error = 'Reconnect request failed — is the backend running?';
      }
    });
  }

  buildChart(curve: any[]): void {
    // Backend returns [[timestamp, equity], ...] arrays
    this.chartData = {
      labels: curve.map((p: any) => Array.isArray(p) ? p[0] : (p.time ?? p.date ?? '')),
      datasets: [{
        data: curve.map((p: any) => Array.isArray(p) ? p[1] : p.equity),
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
      }]
    };
  }

  pnlClass(pnl: number): string {
    return pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
  }

  showChargeBreakdown = false;

  metricCards(): { label: string; value: string; icon: string; cls: string }[] {
    if (!this.result?.summary) return [];
    const s = this.result.summary;
    const fmt = (n: number) => '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    return [
      { label: 'Gross P&L',       value: fmt(s.gross_pnl ?? s.total_pnl),   icon: 'bi-currency-rupee',   cls: (s.gross_pnl ?? s.total_pnl) >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Total Charges',   value: fmt(s.total_charges ?? 0),          icon: 'bi-receipt',           cls: 'pnl-negative' },
      { label: 'Net P&L',         value: fmt(s.total_pnl),                   icon: 'bi-wallet2',           cls: s.total_pnl >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Net Return',      value: s.total_return.toFixed(2) + '%',    icon: 'bi-percent',           cls: s.total_return >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Final Equity',    value: fmt(s.final_equity),                icon: 'bi-bank',              cls: '' },
      { label: 'Max Drawdown',    value: s.max_drawdown.toFixed(2) + '%',    icon: 'bi-arrow-down',        cls: 'pnl-negative' },
      { label: 'Win Rate',        value: s.win_rate.toFixed(1) + '%',        icon: 'bi-trophy',            cls: s.win_rate >= 50 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Sharpe Ratio',    value: s.sharpe_ratio?.toFixed(2) ?? 'N/A', icon: 'bi-graph-up',        cls: s.sharpe_ratio >= 1 ? 'pnl-positive' : '' },
      { label: 'Total Trades',    value: String(s.total_trades),             icon: 'bi-list-ol',           cls: '' },
      { label: 'Avg Net / Trade', value: fmt(s.avg_pnl),                     icon: 'bi-cash',              cls: s.avg_pnl >= 0 ? 'pnl-positive' : 'pnl-negative' },
    ];
  }
}
