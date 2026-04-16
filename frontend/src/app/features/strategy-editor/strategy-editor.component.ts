import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-strategy-editor',
  imports: [CommonModule, FormsModule],
  templateUrl: './strategy-editor.component.html',
})
export class StrategyEditorComponent implements OnInit {
  name = '';
  category = 'equity';
  description = '';
  code = DEFAULT_STRATEGY_CODE;
  isNew = true;
  saving = false;
  saveMsg = '';
  saveError = '';
  categories = ['equity', 'futures', 'options'];

  private editor: any = null;

  constructor(private route: ActivatedRoute, private api: ApiService) {}

  ngOnInit(): void {
    const paramName = this.route.snapshot.paramMap.get('name');
    if (paramName) {
      this.isNew = false;
      this.name = paramName;
      this.loadStrategy(paramName);
    }
    this.initMonaco();
  }

  loadStrategy(name: string): void {
    this.api.getStrategy(name).subscribe({
      next: (s) => {
        this.name = s.name;
        this.category = s.category;
        this.description = s.description || '';
        this.code = s.code || DEFAULT_STRATEGY_CODE;
        if (this.editor) this.editor.setValue(this.code);
      },
      error: () => { this.saveError = 'Could not load strategy.'; }
    });
  }

  initMonaco(): void {
    const loaderScript = document.createElement('script');
    loaderScript.src = 'assets/monaco/vs/loader.js';
    loaderScript.onload = () => {
      (window as any).require.config({ paths: { vs: 'assets/monaco/vs' } });
      (window as any).require(['vs/editor/editor.main'], () => {
        this.editor = (window as any).monaco.editor.create(
          document.getElementById('monaco-container')!,
          {
            value: this.code,
            language: 'python',
            theme: 'vs-dark',
            fontSize: 14,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            automaticLayout: true,
          }
        );
        this.editor.onDidChangeModelContent(() => {
          this.code = this.editor.getValue();
        });
      });
    };
    document.head.appendChild(loaderScript);
  }

  save(): void {
    this.saveError = '';
    this.saveMsg = '';
    if (!this.name.trim()) { this.saveError = 'Strategy name is required.'; return; }
    this.saving = true;
    const payload = { name: this.name, category: this.category, description: this.description, code: this.code };
    const call = this.isNew ? this.api.addStrategy(payload) : this.api.editStrategy(this.name, payload);
    call.subscribe({
      next: () => { this.saveMsg = 'Saved!'; this.saving = false; this.isNew = false; setTimeout(() => this.saveMsg = '', 3000); },
      error: (e) => { this.saveError = e?.error?.detail || 'Save failed.'; this.saving = false; }
    });
  }
}

const DEFAULT_STRATEGY_CODE = `# AI Trading Agent — Custom Strategy Template
# The class must be named "Strategy" and have a generate(df) method.
# df columns: timestamp, open, high, low, close, volume
# Return signals: 1 = buy, -1 = sell, 0 = hold
import pandas as pd

class Strategy:
    def __init__(self):
        self.fast_period = 9
        self.slow_period = 21

    def generate(self, df: pd.DataFrame) -> pd.Series:
        fast_ema = df['close'].ewm(span=self.fast_period, adjust=False).mean()
        slow_ema = df['close'].ewm(span=self.slow_period, adjust=False).mean()
        signal = pd.Series(0, index=df.index)
        signal[fast_ema > slow_ema] = 1
        signal[fast_ema < slow_ema] = -1
        return signal
`;
