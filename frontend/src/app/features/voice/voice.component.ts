import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-voice',
  imports: [CommonModule],
  templateUrl: './voice.component.html',
})
export class VoiceComponent implements OnInit, OnDestroy {
  commands: any[] = [];
  transcript = '';
  result = '';
  listening = false;
  error = '';
  commandHistory: { text: string; result: string; time: string }[] = [];

  private recognition: any;
  private hasWebSpeech = false;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.api.getVoiceCommands().subscribe({ next: (c) => this.commands = c });
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      this.hasWebSpeech = true;
      this.recognition = new SpeechRecognition();
      this.recognition.lang = 'en-IN';
      this.recognition.continuous = false;
      this.recognition.interimResults = false;
      this.recognition.onresult = (event: any) => {
        const text = event.results[0][0].transcript;
        this.transcript = text;
        this.listening = false;
        this.execute(text);
      };
      this.recognition.onerror = () => {
        this.error = 'Microphone error. Check browser permissions.';
        this.listening = false;
      };
      this.recognition.onend = () => { this.listening = false; };
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
      this.recognition.start();
    } else {
      // fallback: use backend microphone
      this.error = '';
      this.listening = true;
      this.api.voiceListen().subscribe({
        next: (r: any) => {
          this.transcript = r.text;
          this.listening = false;
          if (r.text) this.execute(r.text);
        },
        error: () => { this.error = 'Backend voice listen failed.'; this.listening = false; }
      });
    }
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
