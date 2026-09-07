CREATE TABLE IF NOT EXISTS dict.finance_category (
  component_id TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  label        TEXT,
  culture_varying BOOLEAN NOT NULL,
  CHECK (kind IN ('income', 'expenditure'))
);

INSERT INTO dict.finance_category (component_id, kind, label, culture_varying) VALUES
 ('regular_income_background_income',      'income', 'Background income', false),
 ('regular_income_client_state',           'income', NULL,                false),
 ('regular_income_commerce_raiding',       'income', 'Raiding',           false),
 ('regular_income_tribute',                'income', NULL,                false),
 ('regular_income_vassal',                 'income', 'Vassal tribute',    false),
 ('varying_regular_income_foreign_slot',   'income', 'Foreign Slot Income', true),
 ('varying_regular_income_military_force', 'income', 'Horde buildings',   true),
 ('varying_regular_income_mining',         'income', NULL,                true),
 ('varying_regular_income_taxes',          'income', 'Taxes',             true),
 ('varying_regular_income_trade',          'income', 'Trade',             true),
 ('regular_expenditure_army_upkeep',       'expenditure', 'Army upkeep',      false),
 ('regular_expenditure_building_upkeep',   'expenditure', 'Building upkeep',  false),
 ('regular_expenditure_client_state',      'expenditure', NULL,               false),
 ('regular_expenditure_foreign_slot_upkeep','expenditure','Foreign Slot Upkeep', true),
 ('regular_expenditure_loan_repayment',    'expenditure', NULL,               false),
 ('regular_expenditure_navy_upkeep',       'expenditure', 'Black Ark upkeep', false),
 ('regular_expenditure_policing',          'expenditure', NULL,               false),
 ('regular_expenditure_tribute',           'expenditure', NULL,               false),
 ('regular_expenditure_vassal',            'expenditure', 'Master',           false),
 ('regular_expenditure_wages',             'expenditure', NULL,               false)
ON CONFLICT (component_id) DO NOTHING;

CREATE OR REPLACE VIEW corpus.finance_panel_unknown AS
SELECT DISTINCT r.component_id, r.label, r.kind
  FROM corpus.finance_panel_row r
 WHERE r.kind IN ('income', 'expenditure')
   AND NOT EXISTS (SELECT 1 FROM dict.finance_category c
                    WHERE c.component_id = r.component_id);
