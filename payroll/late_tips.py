"""Pay only the unpaid part of a tip on an already-paid booking.

The latest uploaded booking total is authoritative. A receipt date is not
present in the export, so discovery happens on upload, never by polling
Sitterwise. Finished runs retain their own immutable tip snapshots.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path

from .engine import apply_adjustments
from .importer import import_export
from .money import money
from .rules import Rules
from .validate import Finding, STOP

ZERO = Decimal('0')


def job_tip(job, amount=None, kind='booking'):
    return dict(booking_id=job.booking_id, caregiver_key=job.caregiver_key,
                caregiver_id=job.caregiver_id, caregiver_name=job.display_name,
                workday=job.workday.isoformat(), client_name=job.client_name,
                amount=str(money(job.tip if amount is None else amount)), kind=kind)


def payments_for(run):
    return [job_tip(j) for c in run.caregivers for j in c.jobs] + list(run.late_tips)


def save_payments(db, run_id, payments):
    if len({p['booking_id'] for p in payments}) != len(payments):
        raise ValueError('A booking cannot be paid twice in one payroll.')
    db.execute('DELETE FROM tip_payments WHERE run_id=?', (run_id,))
    db.executemany('INSERT INTO tip_payments (run_id,booking_id,amount,details) VALUES (?,?,?,?)',
                   [(run_id, p['booking_id'], p['amount'], json.dumps(p)) for p in payments])
    db.execute('UPDATE runs SET tips_recorded=1 WHERE id=?', (run_id,))


def initialise_history(store):
    """Recover actual tip payments from each saved original export, once.

    Never infer an unknown balance as zero. A missing/damaged source is left
    uninitialised, and only an affected late tip will require attention.
    """
    for record in store.list_runs():
        if record['status'] != 'finalized' or record['tips_recorded']:
            continue
        try:
            source = Path(record['source_path'])
            if not source.is_absolute():
                source = store.path.parent / source
            result = import_export(source, Rules.from_snapshot(json.loads(record['rules_snapshot'])))
            if result.source_sha256 != record['source_sha256']:
                continue
            paid_ids = {r[0] for r in store.db.execute(
                'SELECT booking_id FROM paid_bookings WHERE run_id=?', (record['id'],))}
            jobs = [j for j in result.jobs if j.booking_id in paid_ids and j.is_payable
                    and j.workday and record['period_start'] <= j.workday.isoformat() <= record['period_end']]
            if len(jobs) != len(paid_ids) or len({j.booking_id for j in jobs}) != len(jobs):
                continue
            jobs = apply_adjustments(jobs, store.adjustments(record['id']))
            total = money(sum((j.tip for j in jobs), ZERO))
            expected = json.loads(record['totals_snapshot'] or '{}').get('tips')
            if expected is None or total != money(expected) or any(j.tip < 0 for j in jobs):
                continue
            with store.db:
                save_payments(store.db, record['id'], [job_tip(j) for j in jobs])
        except (OSError, ValueError, KeyError, TypeError):
            # A newer upload must not become evidence of what an old check paid.
            continue


def observe_export(store, result):
    """Remember updated tip totals even when a later month's file omits them."""
    initialise_history(store)
    counts = Counter(j.booking_id for j in result.jobs)
    with store.db:
        for job in result.jobs:
            if not job.booking_id or not job.workday or not job.caregiver_key:
                continue
            if job.tip_was_blank:
                continue  # blank means unknown; it must not erase a known tip
            problem = ''
            if counts[job.booking_id] > 1:
                problem = 'This booking appears more than once in the uploaded file.'
            elif job.tip < 0:
                problem = 'A negative tip needs a manual correction; it will not be deducted automatically.'
            item = job_tip(job)
            store.db.execute('''INSERT INTO tip_updates
                (booking_id,amount,details,source_sha256,problem) VALUES (?,?,?,?,?)
                ON CONFLICT(booking_id) DO UPDATE SET amount=excluded.amount,
                details=excluded.details,source_sha256=excluded.source_sha256,problem=excluded.problem''',
                (job.booking_id, item['amount'], json.dumps(item), result.source_sha256, problem))


def _same_person(a, b):
    if a.get('caregiver_id') and b.get('caregiver_id'):
        return a['caregiver_id'] == b['caregiver_id']
    return a['caregiver_key'] == b['caregiver_key']


def for_run(store, record, reserve=True):
    """Claim outstanding tips for one draft; other drafts cannot also pay them."""
    if record['status'] == 'finalized' or record.get('tip_export_snapshot') is not None:
        return json.loads(record['late_tips_snapshot'] or '[]'), []
    initialise_history(store)
    tips, problems = [], []
    paid_bookings = store.previously_paid()
    with store.db:
        for row in store.db.execute('SELECT * FROM tip_updates ORDER BY booking_id').fetchall():
            item = json.loads(row['details'])
            if item['workday'] >= record['period_start'] or row['booking_id'] not in paid_bookings:
                continue
            entries = store.db.execute('''SELECT t.*,r.period_end FROM tip_payments t
                JOIN runs r ON r.id=t.run_id WHERE t.booking_id=? AND r.status='finalized' ''',
                (row['booking_id'],)).fetchall()
            originals = [e for e in entries if json.loads(e['details'])['kind'] == 'booking']
            problem = row['problem']
            if len(originals) != 1:
                if money(row['amount']) <= 0 and not problem:
                    continue
                problem = 'The app cannot verify how much tip was paid on the original payroll.'
            else:
                original = json.loads(originals[0]['details'])
                if record['period_start'] <= max(e['period_end'] for e in entries):
                    continue  # never add money to an already-paid or earlier period
                if not _same_person(item, original):
                    problem = 'The caregiver on this booking changed after its original payroll.'
                item['caregiver_id'] = item['caregiver_id'] or original['caregiver_id']
            owner = store.get_run(row['claimed_run_id']) if row['claimed_run_id'] else None
            if owner and owner['status'] == 'open' and owner['id'] != record['id']:
                continue
            if problem:
                problems.append(Finding('late_tip_unverified', STOP,
                    f"Check the late tip for {item['caregiver_name']}", problem,
                    'Check the original payment and upload the corrected booking export or restore its original history.',
                    '', item['caregiver_name'], [item['booking_id']]))
                continue
            paid = money(sum((Decimal(e['amount']) for e in entries), ZERO))
            extra = money(Decimal(row['amount']) - paid)
            if extra <= 0:
                continue  # never claw back tips or pay the same amount twice
            item.update(amount=str(extra), total_tip=row['amount'], previously_paid=str(paid), kind='late')
            tips.append(item)
            if reserve:
                store.db.execute('UPDATE tip_updates SET claimed_run_id=? WHERE booking_id=?',
                                 (record['id'], row['booking_id']))
    return tips, problems
