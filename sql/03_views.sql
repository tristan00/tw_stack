CREATE VIEW corpus.turn_bounds AS
  SELECT s.campaign_id, s.turn, MIN(s.snapshot_id) AS open_id, MAX(s.snapshot_id) AS close_id
  FROM corpus.snapshot s JOIN corpus.decision d ON d.decision_id = s.snapshot_id
  GROUP BY s.campaign_id, s.turn;

CREATE VIEW corpus.turn_open AS
  SELECT b.campaign_id, b.turn, b.open_id AS decision_id, sc.income, sc.settlements, sc.allies, sc.vassals, sc.power_rank, sc.lord_level, s.ts
  FROM corpus.turn_bounds b JOIN corpus.snapshot s ON s.snapshot_id = b.open_id
  JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = b.open_id;

CREATE VIEW corpus.campaign_gains AS
  SELECT campaign_id, campaign_key, first_ts, last_ts, n_decisions,
         first_settlements, peak_settlements, peak_settlements - first_settlements AS settlements_gained,
         first_lord_level, peak_lord_level, peak_lord_level - first_lord_level AS levels_gained,
         allies_max, vassals_max, turns
  FROM corpus.campaign WHERE first_snapshot_id IS NOT NULL;

CREATE VIEW corpus.start_counts AS
  SELECT campaign_map_id, faction_id, COUNT(*) AS n FROM corpus.campaign
  WHERE n_decisions > 0 GROUP BY campaign_map_id, faction_id;

CREATE VIEW corpus.campaign_ending AS
  SELECT p.campaign_id, c.campaign_key, p.ts, p.faction_id, p.outcome_id, p.when_text, p.error,
         p.plausibility_verdict AS verdict, p.growth_reason, p.growth_turn, p.growth_min_gain
  FROM corpus.postmortem p JOIN corpus.campaign c USING (campaign_id)
  WHERE p.postmortem_id = (SELECT MAX(postmortem_id) FROM corpus.postmortem q WHERE q.campaign_id = p.campaign_id);

CREATE VIEW analytics2.model_generation AS
  SELECT trial, generation, ts AS seg_from_ts,
         LEAD(ts) OVER (ORDER BY ts) AS seg_to_ts, campaigns, corpus_n_decisions AS corpus_decisions
  FROM ops.trial WHERE NOT archived;
