#!/usr/bin/env python3
"""Import a Roll20 JSON export or folder of pages, then build app aggregates."""
import argparse
from pathlib import Path
from roll20_parse import parse
from build_data import write_report
import sqlite3


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('export',type=Path,help='Roll20 JSON file, HTML file, or folder of exported pages')
    p.add_argument('--db',type=Path,default=Path('data/campaign.sqlite'))
    p.add_argument('--config',type=Path,default=Path('campaign.json'))
    p.add_argument('--report',type=Path,default=Path('public/report.json'))
    p.add_argument('--expect-pages',type=int,help='Optional exact number of numbered pages')
    a=p.parse_args()
    if not a.export.exists(): p.error(f'Export does not exist: {a.export}')
    if not a.config.exists(): p.error(f'Config does not exist: {a.config}; copy campaign.example.json')
    a.db.parent.mkdir(parents=True,exist_ok=True)
    counts=parse(a.export,a.db,a.expect_pages)
    report=write_report(a.db,a.config,a.report)
    print('Imported:',', '.join(f'{k}={v}' for k,v in counts.items()))
    print('Database:',a.db.resolve())
    print('Presentation data:',a.report.resolve())
    print('Roll20 accounts (use these IDs in campaign.json for names/roles):')
    with sqlite3.connect(a.db) as db:
        for pid,alias,n in db.execute('SELECT player_id,who,COUNT(*) AS n FROM messages WHERE player_id IS NOT NULL GROUP BY player_id,who ORDER BY player_id,n DESC'):
            print(f'  {pid}  {alias}  ({n} messages)')
    print(f"Public roll posts: {report['meta']['rollMessages']}")

if __name__=='__main__': main()
