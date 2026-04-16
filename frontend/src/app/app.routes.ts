import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
  {
    path: 'dashboard',
    loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent)
  },
  {
    path: 'strategy-editor',
    loadComponent: () => import('./features/strategy-editor/strategy-editor.component').then(m => m.StrategyEditorComponent)
  },
  {
    path: 'strategy-editor/:name',
    loadComponent: () => import('./features/strategy-editor/strategy-editor.component').then(m => m.StrategyEditorComponent)
  },
  {
    path: 'backtest',
    loadComponent: () => import('./features/backtest/backtest.component').then(m => m.BacktestComponent)
  },
  {
    path: 'live-trading',
    loadComponent: () => import('./features/live-trading/live-trading.component').then(m => m.LiveTradingComponent)
  },
  {
    path: 'voice',
    loadComponent: () => import('./features/voice/voice.component').then(m => m.VoiceComponent)
  },
  { path: '**', redirectTo: 'dashboard' }
];
