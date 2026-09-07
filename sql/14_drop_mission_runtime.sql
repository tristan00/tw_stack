ALTER TABLE corpus.mission_set_member
  DROP COLUMN IF EXISTS target_region_id,
  DROP COLUMN IF EXISTS target_faction_id,
  DROP COLUMN IF EXISTS target_character_cqi,
  DROP COLUMN IF EXISTS quest_character_cqi,
  DROP COLUMN IF EXISTS turn_issued;

ALTER TABLE corpus.snapshot_campaign
  DROP COLUMN IF EXISTS mission_payload_set_id;

DROP TABLE IF EXISTS corpus.mission_payload_set_member;
