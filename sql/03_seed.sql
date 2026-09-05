INSERT INTO dict.faction (key, is_reference, note) VALUES
 ('rebels', false, 'engine pseudo-faction (M3 B)'),
 ('ruins',  false, 'collector masking literal collect.py:406-413'),
 ('wh_main_grn_skull', false, 'truncation artefact diplo_stream.py:14'),
 ('campaign', false, 'actions row 37129 context_id artefact');
INSERT INTO dict.province (key, is_reference, note) VALUES ('', false, 'empty province string on 33/3667 sampled province blobs (M1 A.5)');
INSERT INTO dict.enum (domain, key) VALUES
 ('snapshot_kind','decision'),('snapshot_kind','interrupt'),
 ('entity_kind','campaign'),('entity_kind','lord'),('entity_kind','hero'),('entity_kind','province'),
 ('hostile_kind','army'),('hostile_kind','neutral_army'),('hostile_kind','settlement'),('hostile_kind','hero'),('hostile_kind','neutral_hero'),
 ('skill_status','inactive'),('skill_status','locked_due_to_rank'),('skill_status','active'),('skill_status','locked_by_item'),('skill_status','locked_by_skill'),
 ('mission_status','active'),('mission_status','succeeded'),('mission_status','cancelled'),
 ('merc_action','recruit_ror'),('merc_action','raise_dead'),('merc_action','recruit_blessed'),('merc_action','recruit_imperial'),
 ('gift','small'),('gift','medium'),('gift','large'),
 ('diplo_term','declare_war'),('diplo_term','peace'),('diplo_term','nonaggression_pact'),('diplo_term','trade_agreement'),('diplo_term','defensive_alliance'),('diplo_term','soft_access'),('diplo_term','military_alliance'),('diplo_term','vassal'),('diplo_term','confederation'),
 ('refusal','execute_failed'),('refusal','command_silently_refused'),('refusal','pre_check_refused'),('refusal','executed_unconfirmed'),('refusal','campaign_died'),('refusal','snapshot_failed'),('refusal','awaiting_execution'),('refusal','confirm_unreadable_bus_failure'),
 ('policy','random'),('policy','greedy_catboost'),('policy','greedy_gnn'),('policy','marwil_gnn'),('policy','forced_end_turn'),('policy','greedy_catboost_random_fallback'),
 ('interrupt_kind','pre_battle'),('interrupt_kind','battle_results'),('interrupt_kind','occupation'),('interrupt_kind','dilemma'),('interrupt_kind','diplomacy_proposal'),('interrupt_kind','diplomacy_notice'),('interrupt_kind','war_declared'),('interrupt_kind','event_ack'),('interrupt_kind','declare_war_cancel'),('interrupt_kind','ally_attacked'),
 ('state_at','panel'),('state_at','recorder'),
 ('diplo_event_kind','deal'),('diplo_event_kind','pair_checkpoint'),('diplo_event_kind','campaign_end'),
 ('diplo_channel','outgoing'),('diplo_channel','diplomacy_proposal'),('diplo_channel','diplomacy_notice'),('diplo_channel','ally_attacked'),
 ('outcome','stagnant'),('outcome','unhandled_screen'),('outcome','stuck'),('outcome','defeated'),('outcome','error'),('outcome','completed'),
 ('precheck','treasury_floor'),('precheck','cannot_equip'),('precheck','units_panel_not_open_CTD_guard');
INSERT INTO dict.rite_reason (key) VALUES ('');
INSERT INTO dict.stance (key, is_reference, note) VALUES ('none', false, 'm8: engine literal, not a campaign_stances row');
INSERT INTO corpus.collector_version (collector_sha, note, emits_campaign_meta, emits_pending_queue, emits_v31_block, emits_missions) VALUES
 ('legacy:meta0:pq0', 'sentinel: TW_CODE_VERSION unset, no difficulty/leader/selector, no pending_queue', false, false, false, false),
 ('legacy:meta1:pq0', 'sentinel: TW_CODE_VERSION unset, no pending_queue', true, false, false, false),
 ('legacy:meta1:pq1', 'sentinel: TW_CODE_VERSION unset', true, true, false, false);

INSERT INTO corpus.state_set (kind, hash, n)
SELECT k, sha256(('\x' || lpad(to_hex(k), 4, '0'))::bytea || '\x00000000'::bytea), 0 FROM generate_series(1, 29) k;
