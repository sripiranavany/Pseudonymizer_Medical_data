const express = require('express');
const multer = require('multer');
const axios = require('axios');
const FormData = require('form-data');
const cors = require('cors');
const path = require('path');

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 50 * 1024 * 1024 } });

const N8N_URL = process.env.N8N_URL || 'http://localhost:5678';
const DOC_PROCESSOR_URL = process.env.DOC_PROCESSOR_URL || 'http://localhost:8000';

app.use(cors());
app.use(express.json({ limit: '50mb' }));

// ── Analyze: text / copy-paste ─────────────────────────────────────
app.post('/api/analyze', async (req, res) => {
  try {
    const { document_text, prompt_type, document_id } = req.body;
    if (!document_text?.trim()) return res.status(400).json({ error: 'document_text is required' });

    const docId = document_id || `doc_${Date.now()}`;
    const response = await axios.post(`${N8N_URL}/webhook/pseudonymize`, {
      document_text,
      prompt_type: prompt_type || 'clinical_summary',
      document_id: docId
    }, { timeout: 300000 });

    res.json(response.data);
  } catch (err) {
    const msg = err.response?.data || err.message;
    console.error('Analyze error:', msg);
    res.status(500).json({ error: msg });
  }
});

// ── Analyze: file upload (PDF / TXT / DOCX) ───────────────────────
app.post('/api/analyze/file', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file provided' });

    // Step 1: extract text via doc-processor
    const form = new FormData();
    form.append('file', req.file.buffer, {
      filename: req.file.originalname,
      contentType: req.file.mimetype
    });
    form.append('prompt_type', req.body.prompt_type || 'clinical_summary');
    if (req.body.document_id) form.append('document_id', req.body.document_id);

    const extracted = await axios.post(`${DOC_PROCESSOR_URL}/extract`, form, {
      headers: form.getHeaders(),
      timeout: 30000
    });

    // Step 2: send extracted text to pseudonymizer pipeline
    const response = await axios.post(`${N8N_URL}/webhook/pseudonymize`, {
      document_text: extracted.data.text,
      prompt_type: extracted.data.prompt_type,
      document_id: extracted.data.document_id,
      filename: extracted.data.filename,
      file_type: extracted.data.file_type
    }, { timeout: 300000 });

    res.json(response.data);
  } catch (err) {
    const msg = err.response?.data || err.message;
    console.error('File analyze error:', msg);
    res.status(500).json({ error: msg });
  }
});

// ── History ────────────────────────────────────────────────────────
app.get('/api/history', async (req, res) => {
  try {
    const response = await axios.get(`${DOC_PROCESSOR_URL}/db/results`);
    res.json(response.data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/history/:id', async (req, res) => {
  try {
    const response = await axios.get(`${DOC_PROCESSOR_URL}/db/results/${req.params.id}`);
    res.json(response.data);
  } catch (err) {
    res.status(err.response?.status || 500).json({ error: err.message });
  }
});

// ── Health ─────────────────────────────────────────────────────────
app.get('/api/health', (req, res) => res.json({ status: 'ok' }));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Express API running on http://localhost:${PORT}`));
