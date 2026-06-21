-- AI Pseudonymizer — PostgreSQL Schema

CREATE TABLE IF NOT EXISTS documents (
    id            VARCHAR(150) PRIMARY KEY,
    filename      VARCHAR(255),
    file_type     VARCHAR(20)  NOT NULL DEFAULT 'text',
    raw_text      TEXT         NOT NULL,
    prompt_type   VARCHAR(50)  NOT NULL DEFAULT 'clinical_summary',
    char_count    INTEGER,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS results (
    id                  SERIAL       PRIMARY KEY,
    document_id         VARCHAR(150) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    pseudonymized_text  TEXT,
    raw_llm_output      TEXT,
    restored_analysis   TEXT,
    mapping             JSONB,
    pii_entity_count    INTEGER,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evaluations (
    id               SERIAL       PRIMARY KEY,
    document_id      VARCHAR(150) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    precision_score  FLOAT,
    recall_score     FLOAT,
    f1_score         FLOAT,
    true_positives   INTEGER,
    false_negatives  INTEGER,
    residual_fakes   INTEGER,
    total_pii        INTEGER,
    appeared_in_llm  INTEGER,
    details          JSONB,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_results_document_id    ON results(document_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_document_id ON evaluations(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_created_at   ON documents(created_at DESC);
