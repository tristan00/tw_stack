INSERT INTO dict.family (family, ref_tbl, ref_col, loc_col, note)
VALUES ('effect_scope', 'campaign_effect_scopes', 'key', NULL,
        'engine enum strings from effect:scope(); no loc')
ON CONFLICT (family) DO NOTHING;

CREATE TABLE IF NOT EXISTS dict.effect_scope (
  id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key           TEXT NOT NULL UNIQUE,
  is_reference  BOOLEAN NOT NULL,
  ref_build_id  INTEGER,
  note          TEXT,
  CHECK (is_reference = (ref_build_id IS NOT NULL))
);

ALTER TABLE corpus.effect_set_member
  DROP CONSTRAINT IF EXISTS effect_set_member_scope_id_fkey;
ALTER TABLE corpus.effect_set_member
  ADD CONSTRAINT effect_set_member_scope_id_fkey
  FOREIGN KEY (scope_id) REFERENCES dict.effect_scope;
