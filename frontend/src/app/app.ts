import { Component, OnInit, signal } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { CommonModule } from '@angular/common';
import { ApiService } from './core/services/api.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  sidebarOpen = false;
  angelConnected = false;

  navItems = [
    { path: '/dashboard',       icon: 'bi-grid-1x2',        label: 'Dashboard' },
    { path: '/backtest',        icon: 'bi-bar-chart-line',   label: 'Backtest' },
    { path: '/live-trading',    icon: 'bi-activity',         label: 'Live Trading' },
    { path: '/strategy-editor', icon: 'bi-code-square',      label: 'Strategy Editor' },
    { path: '/voice',           icon: 'bi-mic',              label: 'Voice Commands' },
  ];

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.api.ping().subscribe({
      next: (res: any) => this.angelConnected = !!res?.angel_connected,
      error: () => this.angelConnected = false,
    });
  }

  toggleSidebar(): void { this.sidebarOpen = !this.sidebarOpen; }
  closeSidebar(): void  { this.sidebarOpen = false; }
}
