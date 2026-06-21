import { Component, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule, DatePipe } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ApiService, HistoryItem } from '../../services/api.service';

@Component({
  selector: 'app-history',
  standalone: true,
  imports: [
    CommonModule, DatePipe,
    MatCardModule, MatTableModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatProgressSpinnerModule, MatTooltipModule
  ],
  template: `
    <div class="page-container">

      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;">
        <div>
          <h2 style="font-size:22px;font-weight:700;color:#1a365d;">Analysis History</h2>
          <p style="color:#718096;font-size:14px;">All past analyses stored in PostgreSQL</p>
        </div>
        <button mat-raised-button color="primary" (click)="router.navigate(['/'])">
          <mat-icon>add</mat-icon> New Analysis
        </button>
      </div>

      @if (loading()) {
        <div style="text-align:center;padding:80px;">
          <mat-spinner style="margin:0 auto;"></mat-spinner>
        </div>
      }

      @if (!loading() && items().length === 0) {
        <div class="empty-state">
          <mat-icon>history</mat-icon>
          <p style="font-weight:600;margin-bottom:8px;">No analyses yet</p>
          <p style="font-size:14px;">Run your first document analysis to see results here.</p>
          <button mat-raised-button color="primary" style="margin-top:20px;" (click)="router.navigate(['/'])">
            Start Analyzing
          </button>
        </div>
      }

      @if (!loading() && items().length > 0) {
        <mat-card style="padding:0;overflow:hidden;">
          <table mat-table [dataSource]="items()" class="history-table">

            <!-- Document -->
            <ng-container matColumnDef="document">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;">Document</th>
              <td mat-cell *matCellDef="let row" style="padding:16px;">
                <div style="font-weight:500;color:#2d3748;">{{ row.filename || row.id }}</div>
                <div style="font-size:12px;color:#a0aec0;margin-top:2px;">{{ row.id }}</div>
              </td>
            </ng-container>

            <!-- Type -->
            <ng-container matColumnDef="type">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;">Analysis</th>
              <td mat-cell *matCellDef="let row" style="padding:16px;">
                <span class="pii-chip">{{ promptLabel(row.prompt_type) }}</span>
              </td>
            </ng-container>

            <!-- File type -->
            <ng-container matColumnDef="file_type">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;">Format</th>
              <td mat-cell *matCellDef="let row" style="padding:16px;">
                <mat-icon [matTooltip]="row.file_type" style="color:#a0aec0;font-size:20px;">
                  {{ fileIcon(row.file_type) }}
                </mat-icon>
              </td>
            </ng-container>

            <!-- Score -->
            <ng-container matColumnDef="score">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;">F1 Score</th>
              <td mat-cell *matCellDef="let row" style="padding:16px;">
                <span [style.color]="scoreColor(row.f1_score)" style="font-weight:700;">
                  {{ row.f1_score != null ? (row.f1_score + '%') : '—' }}
                </span>
              </td>
            </ng-container>

            <!-- Date -->
            <ng-container matColumnDef="date">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;">Date</th>
              <td mat-cell *matCellDef="let row" style="padding:16px;color:#718096;font-size:13px;">
                {{ row.created_at | date:'dd MMM yyyy, HH:mm' }}
              </td>
            </ng-container>

            <!-- Action -->
            <ng-container matColumnDef="action">
              <th mat-header-cell *matHeaderCellDef style="padding:16px;"></th>
              <td mat-cell *matCellDef="let row" style="padding:16px;">
                <button mat-icon-button color="primary" (click)="view(row.id)" matTooltip="View result">
                  <mat-icon>open_in_new</mat-icon>
                </button>
              </td>
            </ng-container>

            <tr mat-header-row *matHeaderRowDef="columns" style="background:#f7fafc;"></tr>
            <tr mat-row *matRowDef="let row; columns: columns;"
                (click)="view(row.id)" style="cursor:pointer;"></tr>
          </table>
        </mat-card>
      }

    </div>
  `
})
export class HistoryComponent implements OnInit {
  items = signal<HistoryItem[]>([]);
  loading = signal(true);
  columns = ['document', 'type', 'file_type', 'score', 'date', 'action'];

  constructor(private api: ApiService, public router: Router) {}

  ngOnInit() {
    this.api.getHistory().subscribe({
      next: (data) => { this.items.set(data.results || []); this.loading.set(false); },
      error: () => this.loading.set(false)
    });
  }

  view(id: string) { this.router.navigate(['/result', id]); }

  promptLabel(type: string): string {
    const map: Record<string, string> = {
      clinical_summary: 'Clinical Summary',
      diagnosis_support: 'Diagnosis',
      icd_coding: 'ICD Coding'
    };
    return map[type] || type;
  }

  fileIcon(type: string): string {
    const map: Record<string, string> = { pdf: 'picture_as_pdf', docx: 'article', txt: 'text_snippet', text: 'text_snippet' };
    return map[type] || 'insert_drive_file';
  }

  scoreColor(score: number): string {
    if (score >= 90) return '#276749';
    if (score >= 70) return '#2b6cb0';
    if (score >= 50) return '#c05621';
    return '#c53030';
  }
}
