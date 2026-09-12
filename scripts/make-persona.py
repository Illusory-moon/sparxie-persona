#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人格写进 AstrBot 的 personas 表

用法:
    python make-persona.py --db /opt/astrbot/data/data_v4.db [--name sparxie] [--md persona/sparxie-qq.md]
"""
import argparse
import datetime
import os
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='AstrBot 的 data_v4.db 路径')
    ap.add_argument('--name', default='sparxie', help='人格 id（默认 sparxie）')
    ap.add_argument('--md', default=None, help='人格 markdown 路径')
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    md = args.md or os.path.join(here, '..', 'persona', 'sparxie-qq.md')
    if not os.path.exists(md):
        print('找不到人格文件:', md); sys.exit(1)
    prompt = open(md, encoding='utf-8').read()

    if not os.path.exists(args.db):
        print('找不到数据库:', args.db); sys.exit(1)
    now = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
    c = sqlite3.connect(args.db)
    cur = c.cursor()
    cur.execute('select count(*) from personas where persona_id=?', (args.name,))
    if cur.fetchone()[0]:
        cur.execute('update personas set system_prompt=?, updated_at=? where persona_id=?',
                    (prompt, now, args.name))
        print('已更新人格:', args.name)
    else:
        cur.execute(
            'insert into personas (persona_id, system_prompt, begin_dialogs, tools, skills,'
            ' created_at, updated_at, sort_order) values (?,?,?,?,?,?,?,?)',
            (args.name, prompt, '[]', None, None, now, now, 0))
        print('已创建人格:', args.name)
    c.commit()
    print('字数:', len(prompt))
    print('当前所有人格:', [r[0] for r in cur.execute('select persona_id from personas')])


if __name__ == '__main__':
    main()