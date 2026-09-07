from __future__ import annotations

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

FILES = ('sql/03_tables.sql', 'sql/03_seed.sql', 'sql/03_views.sql',
         'sql/03_constraints.sql',
         'sql/05_capture_gaps.sql', 'sql/06_capture_gaps_constraints.sql',
         'sql/07_effect_scope_family.sql', 'sql/08_skill_nodes.sql', 'sql/11_event.sql', 'sql/12_merc_pool_duplicates.sql', 'sql/13_drop_broken_slaves.sql', 'sql/14_drop_mission_runtime.sql', 'sql/15_drop_interrupt_payload.sql', 'sql/16_event_ancillary.sql', 'sql/17_battle.sql', 'sql/18_ops.sql', 'sql/19_diplomacy_state_change.sql', 'sql/20_income_sources.sql', 'sql/21_finance_panel.sql', 'sql/22_finance_categories.sql', 'sql/23_finance_value_state.sql', 'sql/24_gap_closure.sql')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    sys.stderr.write('%.3f  db-init %s\n' % (time.time(), msg))


def apply(con):
    t0 = time.time()
    log('apply enter')
    for name in FILES:
        t1 = time.time()
        con.execute(io.open(os.path.join(ROOT, name), encoding='utf-8').read())
        log('%s applied %.0f ms' % (name, (time.time() - t1) * 1000))
    log('apply exit %.0f ms' % ((time.time() - t0) * 1000))


def main():
    con = pg.connect(app_name='tw-db-init', autocommit=True)
    have = con.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'corpus'").fetchone()
    if have:
        log('corpus schema already present, nothing to do')
        con.close()
        return
    apply(con)
    con.close()


if __name__ == '__main__':
    main()
