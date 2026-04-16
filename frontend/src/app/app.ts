import { Component, OnInit, OnDestroy } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { CommonModule } from '@angular/common';
import { ApiService } from './core/services/api.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit, OnDestroy {
  sidebarOpen = false;
  angelConnected = false;
  serverOnline = false;
  showServerCmd = false;
  readonly startCmd = 'cd backend  &&  .venv\\Scripts\\uvicorn main:app --reload --port 8000';

  private pingTimer: any;

  navItems = [
    { path: '/dashboard',       icon: 'bi-grid-1x2',        label: 'Dashboard' },
    { path: '/backtest',        icon: 'bi-bar-chart-line',   label: 'Backtest' },
    { path: '/live-trading',    icon: 'bi-activity',         label: 'Live Trading' },
    { path: '/strategy-editor', icon: 'bi-code-square',      label: 'Strategy Editor' },
    { path: '/voice',           icon: 'bi-mic',              label: 'Voice Commands' },
  ];

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.doPing();
    this.pingTimer = setInterval(() => this.doPing(), 30_000);
  }

  ngOnDestroy(): void {
    clearInterval(this.pingTimer);
  }

  doPing(): void {
    this.api.ping().subscribe({
      next: (res: any) => {
        this.serverOnline = true;
        this.angelConnected = !!res?.angel_connected;
      },
      error: () => {
        this.serverOnline = false;
        this.angelConnected = false;
      },
    });
  }

  toggleServerCmd(): void { this.showServerCmd = !this.showServerCmd; }

  toggleSidebar(): void { this.sidebarOpen = !this.sidebarOpen; }
  closeSidebar(): void  { this.sidebarOpen = false; }
}
