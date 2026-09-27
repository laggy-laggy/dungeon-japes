#!/usr/bin/env python3
"""Produce public presentation aggregates from a Roll20 SQLite archive."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

DICE = (4, 6, 8, 10, 12, 20, 100)
SKILLS = ('Acrobatics','Animal_Handling','Arcana','Athletics','Deception',
          'History','Insight','Intimidation','Investigation','Medicine','Nature',
          'Perception','Performance','Persuasion','Religion','Sleight_Of_Hand',
          'Stealth','Survival')
SAVES = ('Strength Save','Dexterity Save','Constitution Save',
         'Intelligence Save','Wisdom Save','Charisma Save')
PUBLIC = "('general','rollresult')"


def campaign_title(db, config):
    if config.get('title'):
        return config['title']
    row = db.execute('SELECT html FROM source_pages ORDER BY page_record_id LIMIT 1').fetchone()
    if row:
        match = re.search(r'<title>\s*Chat Log for (.+?) Campaign\s*</title>',
                          row[0], re.IGNORECASE | re.DOTALL)
        if match:
            return unescape(match.group(1)).strip()
    return 'Roll20 Wrapped'


def make_report(db, config):
    zone = ZoneInfo(config.get('timezone', 'UTC'))
    def day(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(zone).date()

    identities = config.get('players', {})
    aliases, active_days = defaultdict(Counter), defaultdict(set)
    names, alias_by_sheet = defaultdict(Counter), defaultdict(Counter)
    all_dates = set()
    for pid, who, ms, cid, cname in db.execute(
        'SELECT player_id,who,time_ms,character_id,character_name FROM messages'
    ):
        if not pid or ms is None:
            continue
        aliases[pid][who or 'Unknown'] += 1
        date = day(ms)
        all_dates.add(date)
        active_days[pid].add(date)
        if cid:
            if cname: names[cid][cname] += 1
            if who: alias_by_sheet[cid][who] += 1
    if not all_dates:
        raise ValueError('Archive has no dated messages')
    players = {}
    for pid, recorded in aliases.items():
        entry = identities.get(pid, {})
        role = entry.get('role', 'player')
        if role not in ('player', 'gm', 'guest'):
            raise ValueError(f'Invalid role for {pid}: {role}')
        preferred = entry.get('name') or recorded.most_common(1)[0][0]
        players[pid] = dict(
            id=pid, name=preferred, role=role,
            aliases=[n for n, _ in recorded.most_common(5) if n != preferred],
            activityDays=len(active_days[pid]), rollMessages=0, dice=0,
            d20N=0, d20Sum=0, nat20=0, nat1=0, chat=0, words=0,
            byDie={}, unassignedByDie={}, hist={})

    roll_day, roll_day_by_player, roll_month, chat_month = Counter(), defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    daily_d20 = defaultdict(Counter)
    for _, pid, ms in db.execute(f'''
        SELECT DISTINCT m.message_id,m.player_id,m.time_ms
        FROM rolls r JOIN messages m USING(message_id)
        WHERE r.has_dice=1 AND m.message_type IN {PUBLIC}
    '''):
        if pid not in players: continue
        date=day(ms)
        players[pid]['rollMessages'] += 1
        roll_day[date.isoformat()] += 1
        roll_day_by_player[date.isoformat()][pid] += 1
        roll_month[date.strftime('%Y-%m')][pid] += 1
    for pid, ms, words in db.execute("""
        SELECT player_id,time_ms,text_words FROM messages
        WHERE chat_category='plain_text' AND message_type='general'
    """):
        if pid not in players: continue
        players[pid]['chat'] += 1
        players[pid]['words'] += words or 0
        chat_month[day(ms).strftime('%Y-%m')][pid] += 1

    characters = {}
    for cid in names.keys() | alias_by_sheet.keys():
        label = (names[cid].most_common(1)[0][0] if names[cid]
                 else alias_by_sheet[cid].most_common(1)[0][0] + ' · unnamed sheet')
        characters[cid] = dict(id=cid, name=label, dice=0, d20N=0,
                               d20Sum=0, nat20=0, nat1=0, rollMessages=0,
                               controllers=Counter(), byController={}, byDie={})
    for pid, cid, sides, face, ms in db.execute(f'''
        SELECT d.player_id,d.character_id,d.sides,d.face,m.time_ms
        FROM numbered_die_details d JOIN messages m USING(message_id)
        WHERE m.message_type IN {PUBLIC}
    '''):
        if pid not in players or sides <= 0 or face < 1 or face > sides:
            continue
        p=players[pid]
        p['dice'] += 1
        key=str(sides)
        tally=p['byDie'].setdefault(key, {'n':0,'sum':0})
        tally['n'] += 1; tally['sum'] += face
        if sides in DICE:
            hist=p['hist'].setdefault(key,[0]*sides)
            hist[face-1] += 1
        if sides == 20:
            p['d20N'] += 1; p['d20Sum'] += face
            p['nat20'] += face == 20; p['nat1'] += face == 1
            if p['role'] == 'player':
                tally_day = daily_d20[day(ms).isoformat()]
                tally_day['d20'] += 1
                tally_day['nat20'] += face == 20
                tally_day['nat1'] += face == 1
        if not cid:
            unassigned=p['unassignedByDie'].setdefault(key, {'n':0,'sum':0})
            unassigned['n'] += 1; unassigned['sum'] += face
        if cid:
            ch=characters.setdefault(cid,dict(id=cid,name='Unnamed sheet',dice=0,d20N=0,
                d20Sum=0,nat20=0,nat1=0,rollMessages=0,controllers=Counter(),byController={},byDie={}))
            ch['dice'] += 1; ch['controllers'][pid] += 1
            controlled=ch['byController'].setdefault(pid,dict(dice=0,d20N=0,d20Sum=0,
                nat20=0,nat1=0,rollMessages=0))
            controlled['dice'] += 1
            per_die=ch['byDie'].setdefault(key, {'n':0,'sum':0})
            per_die['n'] += 1; per_die['sum'] += face
            if sides == 20:
                ch['d20N'] += 1; ch['d20Sum'] += face
                ch['nat20'] += face == 20; ch['nat1'] += face == 1
                controlled['d20N'] += 1; controlled['d20Sum'] += face
                controlled['nat20'] += face == 20; controlled['nat1'] += face == 1
    for cid, count in db.execute(f'''
        SELECT r.character_id,COUNT(DISTINCT r.message_id) FROM rolls r
        JOIN messages m USING(message_id)
        WHERE r.has_dice=1 AND r.character_id IS NOT NULL
          AND m.message_type IN {PUBLIC}
        GROUP BY r.character_id
    '''):
        characters[cid]['rollMessages'] = count
    for cid,pid,count in db.execute(f'''
        SELECT r.character_id,r.player_id,COUNT(DISTINCT r.message_id) FROM rolls r
        JOIN messages m USING(message_id)
        WHERE r.has_dice=1 AND r.character_id IS NOT NULL
          AND m.message_type IN {PUBLIC}
        GROUP BY r.character_id,r.player_id
    '''):
        if cid in characters and pid in characters[cid]['byController']:
            characters[cid]['byController'][pid]['rollMessages'] = count
    for ch in characters.values():
        ch['controllers']=[dict(id=pid,name=players[pid]['name'],dice=n)
                           for pid,n in ch['controllers'].most_common()]

    months=[]
    y,m=all_dates and min(all_dates).year,min(all_dates).month
    end=(max(all_dates).year,max(all_dates).month)
    while (y,m) <= end:
        label=f'{y:04d}-{m:02d}'
        rolls=dict(roll_month[label]); chats=dict(chat_month[label])
        months.append(dict(month=label,rolls=sum(rolls.values()),chat=sum(chats.values()),
                           rollsByPlayer=rolls,chatByPlayer=chats))
        y,m = (y+1,1) if m==12 else (y,m+1)
    complete=months[1:-1] if len(months)>2 else months
    by_roll=lambda m:m['rolls']

    skill_counts=Counter()
    skill_by_player=defaultdict(Counter)
    skill_by_character=defaultdict(Counter)
    for pid, cid, label in db.execute('''
        SELECT m.player_id,r.character_id,r.label FROM rolls r
        JOIN messages m USING(message_id)
        WHERE m.message_type='general' AND m.roll_template='simple'
          AND r.role='r1' AND r.character_id IS NOT NULL
          AND r.label IN ({})
          AND EXISTS (SELECT 1 FROM dice d WHERE d.message_id=r.message_id
                      AND d.roll_index=r.roll_index AND d.sides=20)
    '''.format(','.join('?' for _ in SKILLS)), SKILLS):
        if pid in players and players[pid]['role']=='player':
            skill_counts[label] += 1
            skill_by_player[label][pid] += 1
            skill_by_character[label][cid] += 1
    skill_checks=[dict(name=label.replace('_',' '),count=skill_counts[label],
                       byPlayer=dict(skill_by_player[label]),byCharacter=dict(skill_by_character[label]))
                  for label in SKILLS]
    skill_checks.sort(key=lambda item:(-item['count'],item['name']))
    save_counts=Counter()
    save_by_player=defaultdict(Counter)
    save_by_character=defaultdict(Counter)
    for pid, cid, label in db.execute('''
        SELECT m.player_id,r.character_id,r.label FROM rolls r
        JOIN messages m USING(message_id)
        WHERE m.message_type='general' AND m.roll_template='simple'
          AND r.role='r1' AND r.character_id IS NOT NULL
          AND r.label IN ({})
          AND EXISTS (SELECT 1 FROM dice d WHERE d.message_id=r.message_id
                      AND d.roll_index=r.roll_index AND d.sides=20)
    '''.format(','.join('?' for _ in (*SAVES,'Death Save'))), (*SAVES,'Death Save')):
        if pid in players and players[pid]['role']=='player':
            save_counts[label] += 1
            save_by_player[label][pid] += 1
            save_by_character[label][cid] += 1
    saving_throws=dict(abilities=sorted(
        (dict(name=label.removesuffix(' Save'),count=save_counts[label],
              byPlayer=dict(save_by_player[label]),byCharacter=dict(save_by_character[label])) for label in SAVES),
        key=lambda item:(-item['count'],item['name'])),deathSaves=save_counts['Death Save'])

    daily_d20_list=[dict(date=date,d20=counts['d20'],nat20=counts['nat20'],nat1=counts['nat1'])
                    for date,counts in sorted(daily_d20.items())]
    # A streak is successive public roll posts by party players on the same local date,
    # each containing exactly one d20. Multi-d20 and non-d20 posts break the run.
    faces_by_message=defaultdict(list)
    for mid, pid, face in db.execute(f'''
        SELECT d.message_id,m.player_id,d.face FROM dice d
        JOIN messages m USING(message_id)
        WHERE m.message_type IN {PUBLIC} AND d.sides=20
        ORDER BY d.die_id
    '''):
        if pid in players and players[pid]['role']=='player':
            faces_by_message[mid].append(face)
    best_twenty, run = [], []
    def finish_streak():
        nonlocal best_twenty
        if len(run)>len(best_twenty):
            best_twenty=run.copy()
    for mid,pid,who,ms,page in db.execute(f'''
        SELECT m.message_id,m.player_id,m.who,m.time_ms,m.page
        FROM messages m JOIN rolls r USING(message_id)
        WHERE m.message_type IN {PUBLIC} AND r.has_dice=1
        GROUP BY m.message_id
        ORDER BY m.time_ms,m.source_order,m.message_id
    '''):
        if pid not in players or players[pid]['role']!='player':
            continue
        date=day(ms).isoformat()
        if faces_by_message.get(mid)==[20]:
            if run and run[-1]['date']!=date:
                finish_streak(); run=[]
            run.append(dict(messageId=mid,person=players[pid]['name'],alias=who,
                            date=date,page=page))
        else:
            finish_streak(); run=[]
    finish_streak()
    for item in best_twenty:
        row=db.execute("SELECT label FROM rolls WHERE message_id=? AND role='r1' LIMIT 1",
                       (item['messageId'],)).fetchone()
        item['label']=row[0] if row and row[0] else 'D20 roll'
    streaks=dict(nat20=dict(count=len(best_twenty),date=best_twenty[0]['date'],
                             rolls=best_twenty) if len(best_twenty)>=2 else None)

    moments=[]
    for mid, pid, who, ms, page, url, fields in db.execute("""
        SELECT m.message_id,m.player_id,m.who,m.time_ms,m.page,p.archive_url,
               m.template_fields_json
        FROM messages m JOIN source_pages p USING(page_record_id)
        WHERE m.message_type='general'
          AND m.roll_template IN ('simple','atk','atkdmg')
    """):
        if pid not in players or players[pid]['role']!='player': continue
        flags=json.loads(fields)
        mode='advantage' if 'advantage' in flags else ('disadvantage' if 'disadvantage' in flags else '')
        if not mode: continue
        faces=[r[0] for r in db.execute('''
            SELECT d.face FROM dice d JOIN rolls r USING(message_id,roll_index)
            WHERE d.message_id=? AND r.role IN ('r1','r2') AND d.sides=20
            ORDER BY r.roll_index
        ''',(mid,))]
        if faces not in ([20,20],[1,1]): continue
        row=db.execute("SELECT label FROM rolls WHERE message_id=? AND role='r1'",(mid,)).fetchone()
        moments.append(dict(kind='double20' if faces[0]==20 else 'double1',
            person=players[pid]['name'],alias=who,label=row[0] if row and row[0] else 'D20 roll',
            date=day(ms).isoformat(),mode=mode,page=page,url=url))
    moments.sort(key=lambda x:x['date'])
    order={'player':0,'gm':1,'guest':2}
    p_list=sorted(players.values(),key=lambda p:(order[p['role']],-p['rollMessages']))
    c_list=sorted(characters.values(),key=lambda c:-c['dice'])
    return dict(meta=dict(title=campaign_title(db, config),
        subtitle=config.get('subtitle','Our story in dice'),timezone=str(zone),
        first=min(all_dates).isoformat(),last=max(all_dates).isoformat(),
        activityDays=len(all_dates),pageCount=db.execute('SELECT COUNT(*) FROM source_pages').fetchone()[0],
        sourceMessages=db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],
        rollMessages=sum(p['rollMessages'] for p in p_list),
        dice=sum(p['dice'] for p in p_list),d20=sum(p['d20N'] for p in p_list),
        chat=sum(p['chat'] for p in p_list),characterSheets=len(c_list),
        busiestDay=dict(date=roll_day.most_common(1)[0][0],rolls=roll_day.most_common(1)[0][1]) if roll_day else None,
        secondBusiestDay=dict(date=roll_day.most_common(2)[1][0],rolls=roll_day.most_common(2)[1][1]) if len(roll_day)>1 else None,
        busiestMonth=max(complete,key=by_roll)['month'] if complete else None,
        calmestMonth=min(complete,key=by_roll)['month'] if complete else None),
        players=p_list,characters=c_list,months=months,moments=moments,skillChecks=skill_checks,savingThrows=saving_throws,
        dailyRolls=[dict(date=date,rolls=count,byPlayer=dict(roll_day_by_player[date])) for date,count in sorted(roll_day.items())],
        dailyD20=daily_d20_list,streaks=streaks)


def write_report(db_path,config_path,out_path):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('SQLite integrity check failed')
        report=make_report(db,config)
    out_path=Path(out_path);out_path.parent.mkdir(parents=True,exist_ok=True)
    out_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    project=Path(__file__).resolve().parents[1]
    if out_path.resolve()==(project/'public/report.json').resolve() and (project/'dist/index.html').exists():
        import shutil
        shutil.copyfile(out_path, project/'dist/report.json')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('--config',type=Path,default=Path('campaign.json'))
    parser.add_argument('--output',type=Path,default=Path('public/report.json'))
    args=parser.parse_args()
    report=write_report(args.database,args.config,args.output)
    print(f"Wrote {args.output}: {report['meta']['rollMessages']} public roll posts")
