import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export interface AnalysisResult {
  document_id: string;
  prompt_type: string;
  status: string;
  result: { analysis: string; pii_entities_detected: number };
  evaluation: {
    precision: number;
    recall: number;
    f1_score: number;
    true_positives: number;
    false_negatives: number;
    total_pii_mapped: number;
    correctly_restored: Array<{ original: string; fake: string }>;
    not_referenced_by_llm: Array<{ original: string; fake: string }>;
  };
  privacy: { pii_sent_to_external_llm: boolean; pseudonyms_used: number };
}

export interface HistoryItem {
  id: string;
  filename: string | null;
  file_type: string;
  prompt_type: string;
  created_at: string;
  restored_analysis: string;
  pii_entity_count: number;
  precision_score: number;
  recall_score: number;
  f1_score: number;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = environment.apiUrl;

  constructor(private http: HttpClient) {}

  analyzeText(text: string, promptType: string, documentId?: string): Observable<AnalysisResult> {
    return this.http.post<AnalysisResult>(`${this.base}/analyze`, {
      document_text: text,
      prompt_type: promptType,
      document_id: documentId || `doc_${Date.now()}`
    });
  }

  analyzeFile(file: File, promptType: string): Observable<AnalysisResult> {
    const form = new FormData();
    form.append('file', file);
    form.append('prompt_type', promptType);
    return this.http.post<AnalysisResult>(`${this.base}/analyze/file`, form);
  }

  getHistory(): Observable<{ results: HistoryItem[] }> {
    return this.http.get<{ results: HistoryItem[] }>(`${this.base}/history`);
  }

  getResult(id: string): Observable<AnalysisResult> {
    return this.http.get<any>(`${this.base}/history/${id}`).pipe(
      map(d => ({
        document_id: d.id,
        prompt_type: d.prompt_type,
        status: 'success',
        result: {
          analysis: d.restored_analysis || '',
          pii_entities_detected: d.pii_entity_count ?? 0
        },
        evaluation: {
          precision: d.precision_score ?? 0,
          recall: d.recall_score ?? 0,
          f1_score: d.f1_score ?? 0,
          true_positives: d.details?.true_positives ?? 0,
          false_negatives: d.details?.false_negatives ?? 0,
          total_pii_mapped: d.details?.total_pii_mapped ?? d.pii_entity_count ?? 0,
          correctly_restored: d.details?.correctly_restored ?? [],
          not_referenced_by_llm: d.details?.not_referenced_by_llm ?? []
        },
        privacy: { pii_sent_to_external_llm: false, pseudonyms_used: d.pii_entity_count ?? 0 }
      } as AnalysisResult))
    );
  }
}
