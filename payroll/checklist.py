"""A short, persistent reminder list for each payroll; never creates pay."""
import hashlib
import json

from . import extras


def items_for(store, record, run, waiting):
    saved = json.loads(record.get('checklist_state') or '{}')
    applied = store.notes_for_run(record['id'])
    items = [dict(key='odds_ends', title='Review Odds & Ends',
                  detail='Check your Odds & Ends entries, including anything kept outside this app.',
                  available=not waiting,
                  evidence=[waiting, applied, [vars(a) for a in store.adjustments(record['id'])]])]
    for entry in extras.due_in_period(store.list_recurring(active_only=True),
                                      run.period_start, run.period_end):
        pay = extras.recurring_payroll(entry, run.period_start, run.period_end)
        items.append(dict(key='recurring:' + entry['id'],
                          title=f"Verify {entry['person_name']}: ${pay.total_paid:.2f} in OnPay",
                          detail='Check after importing. Add manually only if it is missing from OnPay.',
                          available=True, evidence=entry))
    for item in items:
        item['signature'] = hashlib.sha256(json.dumps(item.pop('evidence'), sort_keys=True,
                                                     default=str).encode()).hexdigest()
        item['checked'] = item['available'] and saved.get(item['key']) == item['signature']
    return items


def save(store, run_id, item, checked):
    state = json.loads(store.get_run(run_id).get('checklist_state') or '{}')
    if checked:
        if not item['available']:
            raise ValueError('Apply or resolve the waiting Odds & Ends entries first.')
        state[item['key']] = item['signature']
    else:
        state.pop(item['key'], None)
    with store.db:
        store.db.execute('UPDATE runs SET checklist_state=? WHERE id=?', (json.dumps(state), run_id))
    store.log('weekly_reminder_checked' if checked else 'weekly_reminder_reopened', item['title'], run_id)
