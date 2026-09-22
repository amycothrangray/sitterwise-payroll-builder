"""Private, branded job breakdowns made from the run's existing calculated figures.

Nothing here calculates payroll policy or changes money. PDFs reconcile to the
same caregiver objects as the OnPay CSV, including historical rule snapshots.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import zipfile
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from xml.sax.saxutils import escape

from .money import money
from .paths import APP_ROOT

ZERO = Decimal('0')
MEMO = 'Your payroll breakdown is in OnPay > Menu > My Files.'
SCHEMA = 2
_FONT_LOCK = threading.Lock()


def clean(value):
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(value or ''))


def dollars(value):
    return f'${Decimal(value):,.2f}'


def _allocate(segments, target):
    """Preserve the engine's once-per-week rounding when showing job cents."""
    raw = [Decimal(s['hours']) * Decimal(s['premium_rate']) for s in segments]
    amounts = [a.quantize(Decimal('.01'), rounding=ROUND_FLOOR) for a in raw]
    pennies = int((target - sum(amounts, ZERO)) * 100)
    if pennies < 0 or pennies > len(amounts):
        raise ValueError('Overtime details do not reconcile. Review this payroll before sharing PDFs.')
    for i in sorted(range(len(raw)), key=lambda i: (-(raw[i]-amounts[i]), i))[:pennies]:
        amounts[i] += Decimal('.01')
    return amounts


def statement_data(run, caregiver):
    c = caregiver
    jobs = []
    for j in sorted(c.jobs, key=lambda j: (j.workday or date.min, j.start.isoformat() if j.start else '', j.booking_id)):
        jobs.append(dict(booking_id=clean(j.booking_id), date=j.workday.isoformat() if j.workday else '',
            shift=f'{j.start:%I:%M %p} - {j.end:%I:%M %p}' if j.start and j.end else '',
            client=clean(j.client_name), tier=clean(j.tier_label), hours=str(j.hours_worked),
            rate=str(j.rate), straight=str(j.straight_pay), minimum_hours=str(j.guarantee_hours),
            minimum_pay=str(j.guarantee_pay), bonus=str(j.bonus+j.lifesaver_bonus), tip=str(j.tip),
            mileage=str(j.mileage_amount), other_reimbursement=str(j.other_reimbursement),
            premiums=[], premium_total='0.00'))
    by_id = {j['booking_id']: j for j in jobs}
    if len(by_id) != len(jobs):
        raise ValueError('Duplicate booking numbers prevent a reliable caregiver breakdown.')
    extras = []
    for w in c.weeks:
        week = f'{w.week_start:%b %d} - {w.week_end:%b %d}'
        for double, target, bonus in ((False,w.ot_premium,w.bonus_ot_premium), (True,w.dt_premium,w.bonus_dt_premium)):
            kind = 'Double-time' if double else 'Overtime'
            segments = [s for s in w.premium_segments if (s['kind']=='double_time') == double]
            base = target - bonus
            if segments:
                for s, amount in zip(segments, _allocate(segments, base)):
                    j = by_id.get(clean(s['booking_id']))
                    if j is None:
                        raise ValueError('An overtime booking is missing from this caregiver breakdown.')
                    j['premiums'].append(dict(kind=kind, hours=s['hours'], rate=s['premium_rate'], amount=str(amount)))
                    j['premium_total'] = str(Decimal(j['premium_total']) + amount)
            elif base:
                # Older saved rules use a weekly weighted premium with no job
                # allocation. Show it honestly, without inventing new amounts.
                hours = w.dt_hours if double else w.ot_hours+w.weekly_ot_hours
                extras.append(dict(label=f'{kind} premium - {week}', amount=str(base),
                    detail=f'{hours:.2f} hours; saved weekly calculation. Weekly weighted rate {dollars(w.regular_rate)}.'))
            if bonus:
                extras.append(dict(label=f'Lifesaver bonus {kind.lower()} premium - {week}',
                    amount=str(bonus), detail='Additional premium on Lifesaver incentives; the bonus itself appears with its job.'))
    for j in jobs:
        j['base'] = str(money(Decimal(j['straight'])+Decimal(j['minimum_pay'])))
        j['total'] = str(money(sum((Decimal(j[k]) for k in
            ('base','premium_total','bonus','tip','mileage','other_reimbursement')), ZERO)))
    for tip in c.late_tips:
        extras.append(dict(label=f"Late tip - booking {clean(tip['booking_id'])}", amount=str(money(tip['amount'])),
            detail=f"Job on {clean(tip['workday'])} · {clean(tip['client_name'])}. Tip only; the job's wages were paid previously."))
    for a in c.adjustments:
        if a.booking_id:
            continue
        extras.append(dict(label='Scheduled pay' if a.kind=='recurring_pay' else
            ('Pay adjustment' if a.taxable else 'Reimbursement adjustment'),
            amount=str(money(a.new_value or '0')), detail='Included in this pay period.'))
    total = sum((Decimal(j['total']) for j in jobs), ZERO) + sum((Decimal(e['amount']) for e in extras), ZERO)
    if money(total) != c.total_paid:
        raise ValueError('Caregiver breakdown totals do not match payroll. Review this payroll before sharing PDFs.')
    return dict(schema=SCHEMA, caregiver=clean(c.name), caregiver_key=clean(c.key), caregiver_id=clean(c.caregiver_id),
        start=run.period_start.isoformat(), end=run.period_end.isoformat(), period=run.label,
        pay_date=run.pay_date.isoformat() if run.pay_date else "",
        jobs=jobs, extras=extras, hours=str(c.hours_worked), minimum_hours=str(c.guarantee_hours),
        ot_hours=str(c.ot_hours), dt_hours=str(c.dt_hours), straight=str(c.straight_pay),
        minimum_pay=str(c.guarantee_pay), ot=str(c.ot_premium), dt=str(c.dt_premium),
        bonus=str(c.bonus), tips=str(c.tips), adjustments=str(c.adjustment_taxable_total),
        taxable=str(c.taxable_earnings), reimbursements=str(c.reimbursements), total=str(c.total_paid))


def filename_for(data):
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    name = re.sub(r'[^A-Za-z0-9_-]+', '-', data['caregiver']).strip('-')[:64] or 'Caregiver'
    return f"{name}-Payroll-{data['start']}-to-{data['end']}-{digest}.pdf"


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with _FONT_LOCK:
        for name, file in (('Poppins','Poppins-Regular.ttf'), ('PoppinsSemi','Poppins-Semibold.ttf'), ('PoppinsBold','Poppins-Bold.ttf')):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(APP_ROOT/'branding'/file)))
        pdfmetrics.registerFontFamily('Poppins',normal='Poppins',bold='PoppinsBold',italic='Poppins',boldItalic='PoppinsBold')


def render_pdf(data):
    # Imports are lazy: browsing payroll does not load a PDF renderer.
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether
    _fonts()
    navy, teal, coral = '#1B3A5C', '#84D0D2', '#F48A91'
    normal = ParagraphStyle('Body',fontName='Poppins',fontSize=8.5,leading=13,textColor=colors.HexColor(navy),spaceAfter=4)
    small = ParagraphStyle('Small',parent=normal,fontSize=7.5,leading=11)
    title = ParagraphStyle('Title',parent=normal,fontName='PoppinsBold',fontSize=21,leading=28,spaceAfter=5)
    heading = ParagraphStyle('Heading',parent=normal,fontName='PoppinsSemi',fontSize=11,leading=17,spaceBefore=9,spaceAfter=6,keepWithNext=True)
    def p(text, style=normal):
        return Paragraph(escape(clean(text)).replace('\n','<br/>'),style)
    def rich(parts, style=small):
        return Paragraph('<br/>'.join(escape(clean(t)) for t in parts if t),style)
    payday = date.fromisoformat(data['pay_date']).strftime('%b %d, %Y') if data.get('pay_date') else ''
    period_line = data['period'] + (f' | Pay date {payday}' if payday else '')
    output = io.BytesIO()
    doc = SimpleDocTemplate(output,pagesize=(612,792),leftMargin=38,rightMargin=38,topMargin=88,bottomMargin=56,
        title=f"{data['caregiver']} - Payroll breakdown - {data['period']}",author='Sitterwise, Inc.')
    def frame(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(navy)); canvas.rect(0,782,612,10,fill=1,stroke=0)
        canvas.setFillColor(colors.HexColor(coral)); canvas.rect(0,782,204,10,fill=1,stroke=0)
        canvas.setFillColor(colors.HexColor(teal)); canvas.rect(408,782,204,10,fill=1,stroke=0)
        # The official logo stays unchanged and is embedded without network access.
        canvas.drawInlineImage(str(APP_ROOT/'branding/sitterwise-logo.jpg'),38,733,width=152.25,height=29.25)
        canvas.setFont('PoppinsSemi',9); canvas.setFillColor(colors.HexColor(navy)); canvas.drawRightString(574,746,'PAYROLL BREAKDOWN')
        canvas.setFont('Poppins',7); canvas.drawRightString(574,733,period_line)
        canvas.setStrokeColor(colors.HexColor('#E3EAEE')); canvas.line(38,46,574,46)
        canvas.setFont('Poppins',6.8); canvas.drawString(38,32,'For job details. Your OnPay pay stub shows taxes, deductions and take-home pay.')
        canvas.drawRightString(574,21,f'Page {_doc.page}')
        canvas.restoreState()
    story=[p(data['caregiver'],title),p('Pay period: '+period_line)]
    hero=Table([[p('TOTAL BEFORE TAXES & DEDUCTIONS',small),p(f"{len(data['jobs'])} jobs · {Decimal(data['hours']):.2f} hours worked",small)],
        [p(dollars(data['total']),title),p('Includes reimbursements of '+dollars(data['reimbursements']),small)]],colWidths=[286,250])
    hero.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#E8F5F5')),('BOX',(0,0),(-1,-1),.5,colors.HexColor(teal)),('LEFTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,0),10),('BOTTOMPADDING',(0,-1),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP')]))
    story += [Spacer(1,8),hero]
    if data['jobs']:
        story.append(p('Your jobs this pay period',heading))
        rows=[[p(v,small) for v in ('JOB / SHIFT','WORKED','BASE PAY','EXTRAS','TOTAL')]]
        for j in data['jobs']:
            extra=[]
            for prem in j['premiums']:
                extra.append(f"{prem['kind']}: {dollars(prem['amount'])}")
                extra.append(f"{Decimal(prem['hours']):.2f} hrs × ${Decimal(prem['rate']):.4f}")
            for key,label in (('bonus','Lifesaver bonus'),('tip','Tip'),('mileage','Mileage reimbursement'),('other_reimbursement','Reimbursement')):
                if Decimal(j[key]): extra.append(f'{label}: {dollars(j[key])}')
            base=[dollars(j['base']),f"{Decimal(j['hours']):.2f} hrs × {dollars(j['rate'])}"]
            if Decimal(j['minimum_pay']):
                base += [f"Minimum top-up: {Decimal(j['minimum_hours']):.2f} hrs",dollars(j['minimum_pay'])]
            rows.append([rich([j['date'],j['client'],j['shift'],'Booking '+j['booking_id'],j['tier']]),
                p(f"{Decimal(j['hours']):.2f} hrs",small),rich(base),rich(extra or ['—']),p(dollars(j['total']),small)])
        table=Table(rows,colWidths=[159,54,108,141,74],repeatRows=1,splitInRow=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8F5F5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor('#E3EAEE')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#FAFCFC')])]))
        story.append(table)
    if data['extras']:
        story.append(p('Other amounts included',heading))
        for extra in data['extras']:
            block=Table([[rich([extra['label'],extra['detail']],normal),p(dollars(extra['amount']))]],colWidths=[447,89])
            block.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor('#E3EAEE')),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
            story.append(block)
    explanation=[]
    if Decimal(data['ot']) or Decimal(data['dt']):
        explanation.append('How overtime appears: base pay already includes every worked hour at the job’s normal rate. Overtime and double-time amounts above are additional premiums, not the full pay for those hours. Premiums are rounded by week; a displayed job amount may differ by one cent from multiplying its rounded rate.')
    if Decimal(data['minimum_hours']):
        explanation.append('Minimum pay: the top-up pays for the minimum booking length. It is separate from actual hours worked and does not add overtime hours.')
    if explanation:
        box=Table([[p('\n'.join(explanation),small)]],colWidths=[536]); box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#FDF0F1')),('BOX',(0,0),(-1,-1),.5,colors.HexColor(coral)),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),8),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10)]))
        story += [Spacer(1,12),box]
    summary=[]
    for key,label in (('straight','Base wages for hours worked'),('minimum_pay','Minimum-pay top-ups'),('ot','Overtime premiums'),('dt','Double-time premiums'),('bonus','Lifesaver bonuses'),('tips','Tips (including late tips)'),('adjustments','Scheduled pay / pay adjustments')):
        if Decimal(data[key]): summary.append([p(label,small),p(dollars(data[key]),small)])
    summary += [[p('Taxable earnings',small),p(dollars(data['taxable']),small)],
                [p('Reimbursements',small),p(dollars(data['reimbursements']),small)],
                [p('Total before taxes & deductions',normal),p(dollars(data['total']),normal)]]
    paired=[]
    for i in range(0,len(summary),2):
        paired.append(summary[i]+(summary[i+1] if i+1<len(summary) else [p(''),p('')]))
    totals=Table(paired,colWidths=[196,72,196,72])
    totals.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#E8F5F5')),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBEFORE',(2,0),(2,-1),.5,colors.HexColor('#84D0D2'))]))
    story.append(KeepTogether([p('Pay period totals',heading),totals]))
    doc.build(story,onFirstPage=frame,onLaterPages=frame)
    return output.getvalue()


def create_archive(run, roster):
    from .exports import clock_user_for
    if not run.caregivers:
        raise ValueError('There are no caregivers to include in this payroll breakdown.')
    buffer=io.BytesIO(); manifest=[]
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for c in run.caregivers:
            data=statement_data(run,c); name=filename_for(data); pdf=render_pdf(data); entry=roster.get(c.key)
            archive.writestr(name,pdf)
            manifest.append(dict(caregiver=c.name,clock_user=clock_user_for(c,entry),
                onpay_employee_id=entry.onpay_employee_id if entry else '',filename=name,
                sha256=hashlib.sha256(pdf).hexdigest(),total=data['total']))
        archive.writestr('manifest.json',json.dumps(dict(period_start=run.period_start.isoformat(),
            period_end=run.period_end.isoformat(),pay_date=run.pay_date.isoformat() if run.pay_date else "",caregivers=manifest),ensure_ascii=False,indent=2))
        archive.writestr('READ ME.txt','PRIVATE PAYROLL FILES\n\nUnzip this folder and give it to Claude Cowork with the task copied from the payroll app.\n'
            'Only each caregiver’s own PDF belongs in their OnPay employee Files. Enable Employee Viewable.\n'
            'Never upload this ZIP, manifest, or a combined PDF to Company Documents or an employee profile.\n'
            'Match names, period and totals first. If payroll changes, download a new folder and task together.\n'
            'Caregivers find their breakdown at OnPay > Menu > My Files.\n')
    return buffer.getvalue()
