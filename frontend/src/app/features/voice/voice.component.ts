import { Component, OnInit, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-voice',
  imports: [CommonModule, FormsModule],
  templateUrl: './voice.component.html',
})
export class VoiceComponent implements OnInit, OnDestroy {
  commands: any[] = [];
  transcript = '';
  interimTranscript = '';
  result = '';
  listening = false;
  error = '';
  manualText = '';
  speechSupported = false;
  commandHistory: { text: string; result: string; time: string }[] = [];

  private recognition: any;
  private hasWebSpeech = false;

  constructor(private api: ApiService, private zone: NgZone) {}

  ngOnInit(): void {
    this.api.getVoiceCommands().subscribe({ next: (c) => this.commands = c });
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      this.hasWebSpeech = true;
      this.speechSupported = true;
      this.recognition = new SpeechRecognition();
      this.recognition.lang = 'en-IN';
      this.recognition.continuous = false;
      this.recognition.interimResults = true;
      this.recognition.onresult = (event: any) => {
        let interim = '';
        let final = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const t = event.results[i][0].transcript;
          if (event.results[i].isFinal) final += t;
          else interim += t;
        }
        this.zone.run(() => {
          if (interim) this.interimTranscript = interim;
          if (final) {
            this.transcript = final;
            this.interimTranscript = '';
            this.listening = false;
            this.execute(final);
          }
        });
      };
      this.recognition.onerror = (e: any) => {
        this.zone.run(() => {
          this.error = 'Microphone error: ' + (e?.error ?? 'check browser permissions');
          this.interimTranscript = '';
          this.listening = false;
        });
      };
      this.recognition.onend = () => {
        this.zone.run(() => { this.interimTranscript = ''; this.listening = false; });
      };
    }
  }

  ngOnDestroy(): void {
    if (this.recognition) this.recognition.abort();
  }

  toggleListen(): void {
    if (this.listening) {
      this.recognition?.stop();
      this.listening = false;
    } else if (this.hasWebSpeech) {
      this.error = '';
      this.transcript = '';
      this.result = '';
      this.listening = true;
      try {
        this.recognition.start();
      } catch (e: any) {
        this.error = 'Could not start mic: ' + (e?.message ?? String(e));
        this.listening = false;
      }
    } else {
      this.error = 'Speech recognition not supported in this browser. Use Chrome or Edge, or type a command below.';
    }
  }

  sendManual(): void {
    const text = this.manualText.trim();
    if (!text) return;
    this.transcript = text;
    this.manualText = '';
    this.result = '';
    this.error = '';
    this.execute(text);
  }

  execute(text: string): void {
    this.api.executeVoiceCommand(text).subscribe({
      next: (r: any) => {
        this.result = r.message || r.result || JSON.stringify(r);
        this.commandHistory.unshift({ text, result: this.result, time: new Date().toLocaleTimeString() });
        if (this.commandHistory.length > 20) this.commandHistory.pop();
      },
      error: (e: any) => { this.result = e?.error?.detail || 'Command failed.'; }
    });
  }
}
