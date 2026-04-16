import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { NgChartsModule } from 'ng2-charts';
import { ChartConfiguration } from 'chart.js';
import { Chart, registerables } from 'chart.js';

Chart.register(...registerables);

@Component({
  selector: 'app-backtest',
  imports: [CommonModule, FormsModule, NgChartsModule],
  templateUrl: './backtest.component.html',
})
export class BacktestComponent implements OnInit {
  // Form
  strategyName = '';
  symbol = 'RELIANCE';
  exchange = 'NSE';
  interval = 'ONE_DAY';
  fromDate = '';
  toDate = '';
  capital = 100000;
  slPct = 1.5;
  tslPct = 2.0;

  instrumentType: 'equity' | 'futures' | 'options' = 'equity';
  instrumentTypes = [
    { value: 'equity',  label: 'Equity' },
    { value: 'futures', label: 'Futures' },
    { value: 'options', label: 'Options' },
  ];

  strategies: any[] = [];
  exchanges = ['NSE', 'BSE', 'NFO', 'MCX'];
  intervals = ['ONE_MINUTE','THREE_MINUTE','FIVE_MINUTE','TEN_MINUTE','FIFTEEN_MINUTE',
               'THIRTY_MINUTE','ONE_HOUR','ONE_DAY'];

  // Results
  running = false;
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
    const oneYearAgo = new Date();
    oneYearAgo.setFullYear(today.getFullYear() - 1);
    this.toDate = this.fmt(today);
    this.fromDate = this.fmt(oneYearAgo);
  }

  ngOnInit(): void {
    this.api.listStrategies().subscribe({ next: (s) => this.strategies = s });
    const sn = this.route.snapshot.queryParamMap.get('strategy');
    if (sn) this.strategyName = sn;
  }

  fmt(d: Date): string { return d.toISOString().split('T')[0]; }

  onInstrumentTypeChange(): void {
    if (this.instrumentType === 'equity') {
      this.exchange = 'NSE';
    } else {
      this.exchange = 'NFO';
    }
  }

  run(): void {
    if (!this.strategyName) { this.error = 'Select a strategy.'; return; }
    this.error = '';
    this.result = null;
    this.running = true;
    const payload = {
      strategy_name: this.strategyName,
      symbol: this.symbol.toUpperCase(),
      exchange: this.exchange,
      instrument_type: this.instrumentType,
      interval: this.interval,
      from_date: this.fromDate,
      to_date: this.toDate,
      capital: this.capital,
      sl_pct: this.slPct,
      tsl_pct: this.tslPct
    };
    this.api.runBacktest(payload).subscribe({
      next: (res) => {
        this.result = res;
        this.buildChart(res.equity_curve);
        this.running = false;
      },
      error: (e) => {
        this.error = e?.error?.detail || 'Backtest failed.';
        this.running = false;
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

  metricCards(): { label: string; value: string; icon: string; cls: string }[] {
    if (!this.result?.summary) return [];
    const s = this.result.summary;
    return [
      { label: 'Total Return',    value: s.total_return.toFixed(2) + '%',     icon: 'bi-percent',          cls: s.total_return >= 0 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Max Drawdown',    value: s.max_drawdown.toFixed(2) + '%',     icon: 'bi-arrow-down',       cls: 'pnl-negative' },
      { label: 'Win Rate',        value: s.win_rate.toFixed(1) + '%',         icon: 'bi-trophy',           cls: s.win_rate >= 50 ? 'pnl-positive' : 'pnl-negative' },
      { label: 'Sharpe Ratio',    value: s.sharpe_ratio?.toFixed(2) ?? 'N/A', icon: 'bi-graph-up',         cls: s.sharpe_ratio >= 1 ? 'pnl-positive' : '' },
      { label: 'Winning Trades',  value: String(s.wins ?? 0),                 icon: 'bi-hand-thumbs-up',   cls: 'pnl-positive' },
      { label: 'Losing Trades',   value: String(s.losses ?? 0),               icon: 'bi-hand-thumbs-down', cls: s.losses > 0 ? 'pnl-negative' : '' },
      { label: 'Total Trades',    value: String(s.total_trades),              icon: 'bi-list-ol',          cls: '' },
      { label: 'Avg PnL / Trade', value: '₹' + (s.avg_pnl | 0),             icon: 'bi-cash',             cls: s.avg_pnl >= 0 ? 'pnl-positive' : 'pnl-negative' },
    ];
  }
}
