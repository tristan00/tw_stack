ALTER TABLE corpus.skill_set_member
  ADD COLUMN IF NOT EXISTS rank_required SMALLINT;

ALTER TABLE corpus.char_state
  ADD COLUMN IF NOT EXISTS skill_prereq_set_id BIGINT;

CREATE TABLE IF NOT EXISTS corpus.skill_prereq_set_member (
  set_id          BIGINT NOT NULL,
  ord             SMALLINT NOT NULL,
  skill_id        INTEGER NOT NULL,
  parent_skill_id INTEGER NOT NULL,
  PRIMARY KEY (set_id, ord)
);

ALTER TABLE corpus.skill_prereq_set_member
  DROP CONSTRAINT IF EXISTS skill_prereq_set_member_set_id_fkey;
ALTER TABLE corpus.skill_prereq_set_member
  ADD CONSTRAINT skill_prereq_set_member_set_id_fkey
  FOREIGN KEY (set_id) REFERENCES corpus.state_set;
ALTER TABLE corpus.skill_prereq_set_member
  DROP CONSTRAINT IF EXISTS skill_prereq_set_member_skill_id_fkey;
ALTER TABLE corpus.skill_prereq_set_member
  ADD CONSTRAINT skill_prereq_set_member_skill_id_fkey
  FOREIGN KEY (skill_id) REFERENCES dict.skill;
ALTER TABLE corpus.skill_prereq_set_member
  DROP CONSTRAINT IF EXISTS skill_prereq_set_member_parent_skill_id_fkey;
ALTER TABLE corpus.skill_prereq_set_member
  ADD CONSTRAINT skill_prereq_set_member_parent_skill_id_fkey
  FOREIGN KEY (parent_skill_id) REFERENCES dict.skill;

ALTER TABLE corpus.char_state
  DROP CONSTRAINT IF EXISTS char_state_skill_prereq_set_id_fkey;
ALTER TABLE corpus.char_state
  ADD CONSTRAINT char_state_skill_prereq_set_id_fkey
  FOREIGN KEY (skill_prereq_set_id) REFERENCES corpus.state_set;
