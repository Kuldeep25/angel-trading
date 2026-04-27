import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged, switchMap } from 'rxjs/operators';
import { NgChartsModule } from 'ng2-charts';
import { ChartConfiguration } from 'chart.js';
import { Chart, registerables } from 'chart.js';
import { ApiService } from '../../core/services/api.service';

Chart.register(...registerables);

@Component({
  selector: 'app-level-strategy',
  standalone: true,
  imports: [CommonModule, FormsModule, NgChartsModule],
  templateUrl: './level-strategy.component.html',
})
export class LevelStrategyComponent implements OnInit, OnDestroy {

  // ── Monitor state ────────────────────────────────────────────────────────
  monitorRunning = false;
  paper = true;
  starting = false;
  stopping = false;

  // ── Config ───────────────────────────────────────────────────────────────
  config: any = {};
  configSaving = false;
  configMsg = '';
  configError = false;

  // ── Alerts ───────────────────────────────────────────────────────────────
  alerts: any[] = [];

  // ── Trades ───────────────────────────────────────────────────────────────
  activeTrades: any[] = [];
  tradeHistory: any[] = [];

  // ── Summary ──────────────────────────────────────────────────────────────
  summary: any = {};

  // ── Backtest form ─────────────────────────────────────────────────────────
  btInstrumentType: 'equity' | 'futures' | 'options' = 'options';
  btExchange  = 'NFO';
  btInterval  = 'FIVE_MINUTE';
  btFromDate  = '';
  btToDate    = '';
  btLevels    = '[{"level": 23500, "type": "RESISTANCE", "next_level": 23600}]';
  btUseCurrentAlerts = false;

  // Symbol typeahead
  btSymbol        = 'NIFTY';
  btSymbolQuery   = 'NIFTY';
  btSymbolResults: any[] = [];
  btShowDropdown  = false;
  btSymbolLoading = false;
  private btSearchSubject = new Subject<{ q: string; type: string }>();
  private btSearchSub?: Subscription;

  instrumentTypes = [
    { value: 'equity',  label: 'Equity (NSE)' },
    { value: 'futures', label: 'Futures (NFO)' },
    { value: 'options', label: 'Options (NFO)' },
  ];

  intervals = [
    'ONE_MINUTE','THREE_MINUTE','FIVE_MINUTE','TEN_MINUTE',
    'FIFTEEN_MINUTE','THIRTY_MINUTE','ONE_HOUR','ONE_DAY',
  ];

  // ── Backtest results ──────────────────────────────────────────────────────
  btRunning          = false;
  btResult:  any     = null;
  btError            = '';
  btNoAlert          = false;
  showChargeBreakdown = false;

  // Equity curve chart
  chartData: ChartConfiguration<'line'>['data'] = { labels: [], datasets: [] };
  chartOptions: ChartConfiguration<'line'>['options'] = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { mode: 'index', intersect: false },
    },
    scales: {
      x: { ticks: { maxTicksLimit: 10, color: '#8b949e' }, grid: { color: '#30363d' } },
      y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
    },
  };

  // ── General ──────────────────────────────────────────────────────────────
  error = '';

  private refreshTimer: any;

  constructor(private api: ApiService) {
    const today = new Date();
    this.btToDate   = this.fmt(today);
    const from = new Date(today);
    from.setMonth(from.getMonth() - 3);
    this.btFromDate = this.fmt(from);
  }

  ngOnInit(): void {
    this.loadAll();
    this.refreshTimer = setInterval(() => this.loadLive(), 30_000);

    // Wire debounced symbol search
    this.btSearchSub = this.btSearchSubject.pipe(
      debounceTime(250),
      distinctUntilChanged((a, b) => a.q === b.q && a.type === b.type),
      switchMap(({ q, type }) => {
        this.btSymbolLoading = true;
        return this.api.searchSymbols(q, type);
      }),
    ).subscribe({
      next: (results) => {
        this.btSymbolResults = results;
        this.btSymbolLoading = false;
        this.btShowDropdown  = true;
      },
      error: () => { this.btSymbolLoading = false; },
    });
  }

  ngOnDestroy(): void {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    this.btSearchSub?.unsubscribe();
  }

  // ── Data loading ─────────────────────────────────────────────────────────

  loadAll(): void {
    this.loadConfig();
    this.loadAlerts();
    this.loadLive();
  }

  loadConfig(): void {
    this.api.getLevelConfig().subscribe({
      next: (cfg) => { this.config = cfg; },
      error: () => {}
    });
  }

  loadAlerts(): void {
    this.api.getLevelAlerts().subscribe({
      next: (res) => { this.alerts = res.alerts || []; },
      error: () => {}
    });
  }

  loadLive(): void {
    this.api.getLevelSummary().subscribe({
      next: (res) => {
        this.summary        = res;
        this.monitorRunning = !!res.monitor_running;
        this.paper          = !!res.paper;
      },
      error: () => {}
    });
    this.api.getLevelActiveTrades().subscribe({
      next: (res) => { this.activeTrades = res.trades || []; },
      error: () => {}
    });
    this.api.getLevelHistory().subscribe({
      next: (res) => { this.tradeHistory = res.trades || []; },
      error: () => {}
    });
  }

  // ── Config actions ────────────────────────────────────────────────────────

  saveConfig(): void {
    this.configSaving = true;
    this.configMsg    = '';
    this.api.saveLevelConfig(this.config).subscribe({
      next:  () => { this.configMsg = 'Saved'; this.configError = false; this.configSaving = false; },
      error: (e) => { this.configMsg = e?.error?.detail || 'Save failed'; this.configError = true; this.configSaving = false; }
    });
  }

  // ── Monitor actions ───────────────────────────────────────────────────────

  startMonitor(): void {
    this.starting = true;
    this.api.startLevelMonitor(this.paper).subscribe({
      next:  () => { this.monitorRunning = true; this.starting = false; this.loadLive(); },
      error: (e) => { this.error = e?.error?.detail || 'Start failed'; this.starting = false; }
    });
  }

  stopMonitor(): void {
    this.stopping = true;
    this.api.stopLevelMonitor().subscribe({
      next:  () => { this.monitorRunning = false; this.stopping = false; },
      error: (e) => { this.error = e?.error?.detail || 'Stop failed'; this.stopping = false; }
    });
  }

  // ── Alert actions ─────────────────────────────────────────────────────────

  deleteAlert(alertId: string): void {
    this.api.deleteLevelAlert(alertId).subscribe({
      next:  () => this.loadAlerts(),
      error: (e) => { this.error = e?.error?.detail || 'Delete failed'; }
    });
  }

  // ── Trade actions ─────────────────────────────────────────────────────────

  manualExit(tradeId: string): void {
    if (!confirm('Exit this trade at current market price?')) return;
    this.api.exitLevelTrade(tradeId).subscribe({
      next:  () => this.loadLive(),
      error: (e) => { this.error = e?.error?.detail || 'Exit failed'; }
    });
  }

  // ── Symbol typeahead ──────────────────────────────────────────────────────

  onBtSymbolInput(): void {
    if (this.btSymbolQuery.length >= 1) {
      this.btSearchSubject.next({ q: this.btSymbolQuery, type: this.btInstrumentType });
    } else {
      this.btSymbolResults = [];
      this.btShowDropdown  = false;
    }
  }

  selectBtSymbol(item: any): void {
    this.btSymbol       = item.name || item.raw_symbol || item.symbol;
    this.btSymbolQuery  = this.btSymbol;
    this.btShowDropdown = false;
    if (this.btInstrumentType !== 'equity' && item.exchange) {
      this.btExchange = item.exchange;
    }
  }

  hideBtDropdown(): void {
    setTimeout(() => {
      this.btShowDropdown = false;
      if (this.btSymbolQuery.trim()) {
        this.btSymbol = this.btSymbolQuery.trim().toUpperCase();
      }
    }, 200);
  }

  onBtInstrumentTypeChange(): void {
    this.btExchange = this.btInstrumentType === 'equity' ? 'NSE' : 'NFO';
    this.btSymbolResults = [];
    this.btShowDropdown  = false;
    if (this.btSymbolQuery.length >= 1) {
      this.btSearchSubject.next({ q: this.btSymbolQuery, type: this.btInstrumentType });
    }
  }

  // ── Backtest ──────────────────────────────────────────────────────────────

  runBacktest(): void {
    let levels: any[] = [];

    if (this.btUseCurrentAlerts) {
      const sym = this.btSymbol.toUpperCase();
      levels = this.alerts.filter(a => a.symbol?.toUpperCase() === sym);
      if (levels.length === 0) {
        this.btNoAlert = true;
        this.btResult  = null;
        this.btError   = '';
        return;
      }
      this.btNoAlert = false;
    } else {
      try {
        levels = JSON.parse(this.btLevels);
      } catch {
        this.btError = 'Levels JSON is invalid. Fix the JSON and try again.';
        return;
      }
      if (!levels.length) {
        this.btNoAlert = true;
        this.btResult  = null;
        this.btError   = '';
        return;
      }
      this.btNoAlert = false;
    }

    this.btRunning = true;
    this.btError   = '';
    this.btResult  = null;

    this.api.runLevelBacktest({
      symbol:          this.btSymbol.toUpperCase(),
      from_date:       this.btFromDate,
      to_date:         this.btToDate,
      levels,
      exchange:        this.btExchange,
      interval:        this.btInterval,
      instrument_type: this.btInstrumentType,
    }).subscribe({
      next: (res) => {
        if (res?.error === 'no_levels') {
          this.btNoAlert = true;
          this.btRunning = false;
          return;
        }
        this.btResult  = res;
        this.btRunning = false;
        this.buildChart(res.equity_curve);
      },
      error: (e) => {
        this.btError   = e?.error?.detail || 'Backtest failed.';
        this.btRunning = false;
      },
    });
  }

  buildChart(curve: any[]): void {
    if (!curve?.length) return;
    this.chartData = {
      labels: curve.map((p: any) => Array.isArray(p) ? p[0] : (p.time ?? p.date ?? '')),
      datasets: [{
        data: curve.map((p: any) => Array.isArray(p) ? p[1] : p.equity),
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
      }],
    };
  }

  // ── Metric tiles (mirrors backtest page) ──────────────────────────────────

  metricCards(): { label: string; value: string; icon: string; cls: string }[] {
    if (!this.btResult?.metrics) return [];
    const m   = this.btResult.metrics;
    const cap = this.config?.capital ?? 100000;
    const finalEquity  = cap + (m.total_pnl ?? 0);
    const totalReturn  = cap > 0 ? (m.total_pnl / cap * 100) : 0;
    const fmt = (n: number) => '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });

    return [
      // ── P&L (0-4) ──────────────────────────────────────────────────────────
      { label: 'Net P&L',        value: fmt(m.total_pnl ?? 0),              icon: 'bi-wallet2',           cls: (m.total_pnl ?? 0) >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Net Return',     value: totalReturn.toFixed(2) + '%',       icon: 'bi-percent',           cls: totalReturn >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Final Equity',   value: fmt(finalEquity),                   icon: 'bi-bank',              cls: '' },
      { label: 'Avg / Trade',    value: fmt(m.avg_pnl ?? 0),                icon: 'bi-cash',              cls: (m.avg_pnl ?? 0) >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Best Trade',     value: fmt(m.best_trade ?? 0),             icon: 'bi-arrow-up-circle',   cls: 'pnl-positive' },
      // ── Trade stats (5-9) ──────────────────────────────────────────────────
      { label: 'Win Rate',       value: (m.win_rate ?? 0).toFixed(1) + '%', icon: 'bi-trophy',            cls: (m.win_rate ?? 0) >= 50 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Winning Trades', value: String(m.wins ?? 0),                icon: 'bi-check-circle',      cls: 'pnl-positive' },
      { label: 'Losing Trades',  value: String(m.losses ?? 0),              icon: 'bi-x-circle',          cls: (m.losses ?? 0) > 0 ? 'pnl-negative' : '' },
      { label: 'Total Trades',   value: String(m.total_trades ?? 0),        icon: 'bi-list-ol',           cls: '' },
      { label: 'Worst Trade',    value: fmt(m.worst_trade ?? 0),            icon: 'bi-arrow-down-circle', cls: 'pnl-negative' },
      // ── Risk (10-11) ───────────────────────────────────────────────────────
      { label: 'Max Drawdown',   value: (m.max_drawdown ?? 0).toFixed(2) + '%', icon: 'bi-arrow-down-circle', cls: 'pnl-negative' },
      { label: 'Sharpe Ratio',   value: (m.sharpe ?? 0).toFixed(2),         icon: 'bi-graph-up',          cls: (m.sharpe ?? 0) >= 1 ? 'pnl-positive' : '' },
    ];
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  private fmt(d: Date): string {
    return d.toISOString().split('T')[0];
  }

  pnlClass(val: number): string {
    return val >= 0 ? 'pnl-positive' : 'pnl-negative';
  }
}

