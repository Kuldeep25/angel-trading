import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

const BASE = 'http://localhost:8000';

@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(private http: HttpClient) {}

  // ── Health ──────────────────────────────────────────────────────────────
  ping(): Observable<any> {
    return this.http.get(`${BASE}/ping`);
  }

  reconnect(): Observable<any> {
    return this.http.post(`${BASE}/reconnect`, {});
  }

  disconnect(): Observable<any> {
    return this.http.post(`${BASE}/disconnect`, {});
  }

  // ── Strategies ──────────────────────────────────────────────────────────
  listStrategies(): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/strategies/list`);
  }

  getStrategy(name: string): Observable<any> {
    return this.http.get(`${BASE}/strategies/${name}`);
  }

  addStrategy(payload: any): Observable<any> {
    return this.http.post(`${BASE}/strategies/add`, payload);
  }

  editStrategy(name: string, payload: any): Observable<any> {
    return this.http.put(`${BASE}/strategies/edit/${name}`, payload);
  }

  deleteStrategy(name: string): Observable<any> {
    return this.http.delete(`${BASE}/strategies/delete/${name}`);
  }

  copyStrategy(sourceName: string, newName: string): Observable<any> {
    return this.http.post(`${BASE}/strategies/copy/${sourceName}/${newName}`, {});
  }

  toggleStrategy(name: string, enabled: boolean): Observable<any> {
    return this.http.patch(`${BASE}/strategies/toggle/${name}?enabled=${enabled}`, {});
  }

  setStrategyMode(name: string, mode: string): Observable<any> {
    return this.http.patch(`${BASE}/strategies/mode/${name}?mode=${mode}`, {});
  }

  // ── Backtest ─────────────────────────────────────────────────────────────
  runBacktest(payload: any): Observable<any> {
    return this.http.post(`${BASE}/backtest`, payload);
  }

  // ── Symbols ──────────────────────────────────────────────────────────────
  searchSymbols(q: string, instrumentType: string, limit = 50): Observable<any[]> {
    const params = new HttpParams()
      .set('q', q)
      .set('instrument_type', instrumentType)
      .set('limit', limit);
    return this.http.get<any[]>(`${BASE}/symbols`, { params });
  }

  // ── Live trading ─────────────────────────────────────────────────────────
  startTrading(payload: any): Observable<any> {
    return this.http.post(`${BASE}/live/start`, payload);
  }

  stopTrading(strategyName?: string, symbol?: string): Observable<any> {
    if (strategyName && symbol) {
      return this.http.post(
        `${BASE}/live/stop?strategy_name=${encodeURIComponent(strategyName)}&symbol=${encodeURIComponent(symbol)}`, {});
    }
    return this.http.post(`${BASE}/live/stop-all`, {});
  }

  tradingStatus(): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/live/status`);
  }

  // ── Positions ────────────────────────────────────────────────────────────
  getPositions(): Observable<any> {
    return this.http.get(`${BASE}/positions`);
  }

  exitPosition(symbol: string, paper = true): Observable<any> {
    return this.http.post(`${BASE}/positions/exit/${symbol}?paper=${paper}`, {});
  }

  exitAllPositions(paper = true): Observable<any> {
    return this.http.post(`${BASE}/positions/exit-all?paper=${paper}`, {});
  }

  // ── Account / Funds ──────────────────────────────────────────────────────
  getAccountFunds(): Observable<any> {
    return this.http.get(`${BASE}/account/funds`);
  }

  // ── Position Guards ──────────────────────────────────────────────────────
  getPositionGuards(): Observable<any> {
    return this.http.get(`${BASE}/positions/guards`);
  }

  setPositionGuard(payload: any): Observable<any> {
    return this.http.post(`${BASE}/positions/guard`, payload);
  }

  removePositionGuard(symbol: string): Observable<any> {
    return this.http.delete(`${BASE}/positions/guard/${encodeURIComponent(symbol)}`);
  }

  // ── Voice ────────────────────────────────────────────────────────────────
  executeVoiceCommand(text: string, mode: 'paper' | 'live' = 'paper'): Observable<any> {
    return this.http.post(`${BASE}/voice/execute`, { text, mode });
  }

  voiceListen(): Observable<any> {
    return this.http.post(`${BASE}/voice/listen`, {});
  }

  getVoiceCommands(): Observable<any> {
    return this.http.get(`${BASE}/voice/commands`);
  }
}
