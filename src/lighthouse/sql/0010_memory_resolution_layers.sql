-- Durable, tier-specific projections of one conversation distillation.
-- `deep` intentionally remains the indexed evidence layer (messages, facts,
-- files and locators); only index/focused are lossy summaries.

CREATE TABLE IF NOT EXISTS lh_memory_distillation_layers (
  conversation_id UUID NOT NULL REFERENCES lh_conversations(id) ON DELETE CASCADE,
  tier TEXT NOT NULL CHECK (tier IN ('index','focused')),
  summary TEXT NOT NULL DEFAULT '',
  entities JSONB NOT NULL DEFAULT '[]'::jsonb,
  relations JSONB NOT NULL DEFAULT '[]'::jsonb,
  uncertainties JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_message_id BIGINT,
  source_hash CHAR(64),
  source_distillation_level INTEGER NOT NULL DEFAULT 1 CHECK (source_distillation_level BETWEEN 0 AND 9),
  model TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (conversation_id,tier)
);

CREATE INDEX IF NOT EXISTS idx_lh_memory_distillation_layers_lookup
  ON lh_memory_distillation_layers(conversation_id,tier,updated_at DESC);
