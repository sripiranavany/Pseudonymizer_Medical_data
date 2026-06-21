import { Component, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatTabsModule } from '@angular/material/tabs';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-analyze',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatTabsModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatButtonModule, MatIconModule,
    MatProgressSpinnerModule, MatSnackBarModule
  ],
  template: `
    <div class="page-container">

      <!-- Hero -->
      <div style="text-align:center; margin-bottom:32px;">
        <mat-icon style="font-size:56px;height:56px;width:56px;color:#2b6cb0;">shield</mat-icon>
        <h1 style="font-size:28px;font-weight:700;color:#1a365d;margin:12px 0 8px;">AI Medical Pseudonymizer</h1>
        <p style="color:#718096;font-size:15px;max-width:520px;margin:0 auto;line-height:1.6;">
          Analyze medical documents with AI while keeping patient data private.
          PII is pseudonymized locally — real data never leaves your network.
        </p>
      </div>

      <mat-card>
        <mat-card-content style="padding:24px;">

          <mat-tab-group>

            <!-- Tab 1: File Upload -->
            <mat-tab>
              <ng-template mat-tab-label>
                <mat-icon style="margin-right:6px;">upload_file</mat-icon> Upload File
              </ng-template>

              <div class="drop-zone"
                   [class.drag-over]="isDragging()"
                   (click)="fileInput.click()"
                   (dragover)="onDragOver($event)"
                   (dragleave)="isDragging.set(false)"
                   (drop)="onDrop($event)">
                <mat-icon>cloud_upload</mat-icon>
                <p style="font-weight:500;margin-bottom:4px;">Drag & drop your document here</p>
                <p>Supported: PDF, TXT, DOCX</p>
                @if (selectedFile()) {
                  <p class="file-selected">
                    <mat-icon>check_circle</mat-icon> {{ selectedFile()!.name }}
                  </p>
                }
              </div>
              <input #fileInput type="file" hidden accept=".pdf,.txt,.docx,.doc"
                     (change)="onFileSelected($event)">
            </mat-tab>

            <!-- Tab 2: Copy-Paste -->
            <mat-tab>
              <ng-template mat-tab-label>
                <mat-icon style="margin-right:6px;">content_paste</mat-icon> Paste Text
              </ng-template>

              <mat-form-field appearance="outline" class="full-width" style="margin-top:20px;">
                <mat-label>Paste medical document text here</mat-label>
                <textarea matInput [(ngModel)]="documentText" rows="10"
                  placeholder="Patient: Hans Müller, DOB: 14.03.1958, Address: Hauptstraße 5...">
                </textarea>
              </mat-form-field>
            </mat-tab>

          </mat-tab-group>

          <!-- Analysis Type -->
          <mat-form-field appearance="outline" class="full-width" style="margin-top:16px;">
            <mat-label>Analysis Type</mat-label>
            <mat-select [(ngModel)]="promptType">
              <mat-option value="clinical_summary">
                <mat-icon>summarize</mat-icon> Clinical Summarization
              </mat-option>
              <mat-option value="diagnosis_support">
                <mat-icon>biotech</mat-icon> Diagnosis Support
              </mat-option>
              <mat-option value="icd_coding">
                <mat-icon>code</mat-icon> ICD-10 Medical Coding
              </mat-option>
            </mat-select>
          </mat-form-field>

          <!-- Submit -->
          <button mat-raised-button color="primary"
                  style="width:100%;height:48px;font-size:15px;font-weight:600;"
                  [disabled]="loading() || (!selectedFile() && !documentText.trim())"
                  (click)="analyze()">
            @if (loading()) {
              <mat-spinner diameter="22" style="display:inline-block;margin-right:8px;"></mat-spinner>
              Analyzing — please wait...
            } @else {
              <mat-icon>psychology</mat-icon>
              Analyze Document
            }
          </button>

        </mat-card-content>
      </mat-card>

      <!-- Info chips -->
      <div style="display:flex;gap:12px;flex-wrap:wrap;justify-content:center;margin-top:8px;">
        <span class="pii-chip"><mat-icon style="font-size:14px;height:14px;width:14px;">lock</mat-icon>PII never sent externally</span>
        <span class="pii-chip"><mat-icon style="font-size:14px;height:14px;width:14px;">memory</mat-icon>Local Ollama detection</span>
        <span class="pii-chip"><mat-icon style="font-size:14px;height:14px;width:14px;">psychology</mat-icon>Gemma 4 31B analysis</span>
        <span class="pii-chip"><mat-icon style="font-size:14px;height:14px;width:14px;">storage</mat-icon>Results saved to PostgreSQL</span>
      </div>

    </div>
  `
})
export class AnalyzeComponent {
  selectedFile = signal<File | null>(null);
  isDragging = signal(false);
  documentText = '';
  promptType = 'clinical_summary';
  loading = signal(false);

  constructor(private api: ApiService, private router: Router, private snack: MatSnackBar) {}

  onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files?.length) this.selectedFile.set(input.files[0]);
  }

  onDragOver(event: DragEvent) {
    event.preventDefault();
    this.isDragging.set(true);
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    this.isDragging.set(false);
    const file = event.dataTransfer?.files[0];
    if (file) this.selectedFile.set(file);
  }

  analyze() {
    this.loading.set(true);
    const file = this.selectedFile();
    const obs = file
      ? this.api.analyzeFile(file, this.promptType)
      : this.api.analyzeText(this.documentText, this.promptType);

    obs.subscribe({
      next: (result) => {
        this.loading.set(false);
        this.router.navigate(['/result', result.document_id], { state: { result } });
      },
      error: (err) => {
        this.loading.set(false);
        this.snack.open('Error: ' + (err.error?.error || err.message), 'Close', { duration: 6000 });
      }
    });
  }
}
