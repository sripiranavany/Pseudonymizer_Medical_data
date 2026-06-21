import { Component, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatChipsModule } from '@angular/material/chips';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService, AnalysisResult } from '../../services/api.service';

@Component({
  selector: 'app-result',
  standalone: true,
  imports: [
    CommonModule, MatCardModule, MatButtonModule, MatIconModule,
    MatProgressBarModule, MatChipsModule, MatDividerModule, MatProgressSpinnerModule
  ],
  template: `
    <div class="page-container">

      <!-- Back + header -->
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
        <button mat-stroked-button (click)="router.navigate(['/'])">
          <mat-icon>arrow_back</mat-icon> New Analysis
        </button>
        <button mat-stroked-button (click)="router.navigate(['/history'])">
          <mat-icon>history</mat-icon> History
        </button>
      </div>

      @if (loading()) {
        <div style="text-align:center;padding:80px;">
          <mat-spinner style="margin:0 auto 20px;"></mat-spinner>
          <p style="color:#718096;">Loading result...</p>
        </div>
      }

      @if (result()) {
        <!-- Privacy banner -->
        <div class="privacy-badge">
          <mat-icon>verified_user</mat-icon>
          {{ result()!.result.pii_entities_detected }} PII entities were pseudonymized —
          real patient data was never sent to the external AI provider.
        </div>

        <!-- Stats row -->
        <div class="stats-row">
          <div class="stat-card">
            <div class="stat-value">{{ result()!.result.pii_entities_detected }}</div>
            <div class="stat-label">PII Detected</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ result()!.evaluation.f1_score }}%</div>
            <div class="stat-label">F1 Score</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ promptLabel(result()!.prompt_type) }}</div>
            <div class="stat-label">Analysis Type</div>
          </div>
        </div>

        <!-- Analysis result -->
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>description</mat-icon>
            <mat-card-title>Analysis Result</mat-card-title>
            <mat-card-subtitle>{{ result()!.document_id }}</mat-card-subtitle>
          </mat-card-header>
          <mat-divider></mat-divider>
          <mat-card-content style="padding:20px;">
            <div class="analysis-text">{{ result()!.result.analysis }}</div>
          </mat-card-content>
        </mat-card>

        <!-- Evaluation scores -->
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>analytics</mat-icon>
            <mat-card-title>De-pseudonymization Accuracy</mat-card-title>
            <mat-card-subtitle>How accurately original PII was restored in the AI output</mat-card-subtitle>
          </mat-card-header>
          <mat-divider></mat-divider>
          <mat-card-content style="padding:20px;">
            <div class="score-row">
              <label>Precision</label>
              <mat-progress-bar mode="determinate" [value]="result()!.evaluation.precision" color="primary"></mat-progress-bar>
              <span>{{ result()!.evaluation.precision }}%</span>
            </div>
            <div class="score-row">
              <label>Recall</label>
              <mat-progress-bar mode="determinate" [value]="result()!.evaluation.recall" color="accent"></mat-progress-bar>
              <span>{{ result()!.evaluation.recall }}%</span>
            </div>
            <div class="score-row">
              <label>F1 Score</label>
              <mat-progress-bar mode="determinate" [value]="result()!.evaluation.f1_score" color="warn"></mat-progress-bar>
              <span>{{ result()!.evaluation.f1_score }}%</span>
            </div>

            <mat-divider style="margin:16px 0;"></mat-divider>
            <p style="font-size:12px;color:#718096;">
              True positives: {{ result()!.evaluation.true_positives }} &nbsp;|&nbsp;
              False negatives: {{ result()!.evaluation.false_negatives }} &nbsp;|&nbsp;
              Total PII mapped: {{ result()!.evaluation.total_pii_mapped }}
            </p>
          </mat-card-content>
        </mat-card>

        <!-- Pseudonymized entities -->
        @if (result()!.evaluation.correctly_restored.length) {
          <mat-card>
            <mat-card-header>
              <mat-icon mat-card-avatar>swap_horiz</mat-icon>
              <mat-card-title>Correctly Restored PII</mat-card-title>
            </mat-card-header>
            <mat-card-content style="padding:16px 20px 20px;">
              <div class="pii-chips">
                @for (item of result()!.evaluation.correctly_restored; track item.original) {
                  <span class="pii-chip">
                    <mat-icon style="font-size:12px;height:12px;width:12px;">check</mat-icon>
                    {{ item.original }}
                  </span>
                }
              </div>
            </mat-card-content>
          </mat-card>
        }
      }

    </div>
  `
})
export class ResultComponent implements OnInit {
  result = signal<AnalysisResult | null>(null);
  loading = signal(false);

  constructor(
    private route: ActivatedRoute,
    public router: Router,
    private api: ApiService
  ) {}

  ngOnInit() {
    // Try router state first (fast path — just came from analyze page)
    const state = history.state?.result as AnalysisResult;
    if (state?.result) {
      this.result.set(state);
      return;
    }

    // Fallback: fetch from backend (e.g. page refresh)
    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      this.loading.set(true);
      this.api.getResult(id).subscribe({
        next: (data) => { this.loading.set(false); this.result.set(data); },
        error: () => this.loading.set(false)
      });
    }
  }

  promptLabel(type: string): string {
    const map: Record<string, string> = {
      clinical_summary: 'Summary',
      diagnosis_support: 'Diagnosis',
      icd_coding: 'ICD-10'
    };
    return map[type] || type;
  }
}
