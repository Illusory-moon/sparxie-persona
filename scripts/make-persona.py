#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人格写进 AstrBot 的 personas 表

用法:
    python make-persona.py --db /opt/astrbot/data/data_v4.db [--name sparxie] [--md persona/sparxie-qq.md]
"""
import argparse
import datetime
import os
import re
import sqlite3
import sys
from contextlib import closing

# 「我的成长记录（自动写入）」—— soul_memory 每次自动往 SOUL.md 追加的那一段 ✓。
# 它是**记忆** ✗ 不是人格正文 ✓（而 SOUL.md 本身还挂在 extra_diaries 里 ✓ 记忆通道已经覆盖它 ✓）。
# 2026-10-08 主人裁定 ✓：同步人格时**一律剔掉** ✗ —— 否则每同步一次，就把越攒越陈的记忆塞进人格 ✓（留尾巴 ✗）。
AUTO_SECTION = re.compile(r'^#{0,3}\s*\**\s*我的成长记录', re.M)


def strip_auto_memory(text):
    """剔掉自动成长段 ✓（从该标题起 ✓ 到下一个同级 '## ' 标题或文末 ✓）。返回 (新文本, 剔掉字数) ✓。"""
    m = AUTO_SECTION.search(text)
    if not m:
        return text, 0
    head = text[:m.start()]
    rest = text[m.start():]
    # ⚠️ 别把**标题自己**当成「下一个小节」✗ —— 先跳过标题那一行再找 ✓
    _nl = rest.find('\n')
    body = rest[_nl + 1:] if _nl >= 0 else ''
    nxt = re.search(r'^## ', body, flags=re.M)
    tail = body[nxt.start():] if nxt else ''
    out = (head.rstrip() + (('\n\n' + tail) if tail else '\n')).rstrip() + '\n'
    return out, len(text) - len(out)


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
    with open(md, encoding='utf-8') as source:
        prompt = source.read()
    prompt, dropped = strip_auto_memory(prompt)
    if dropped:
        print('已剔掉「我的成长记录（自动写入）」%d 字 ✓（那是记忆 ✓ 走记忆通道 ✓ 不进人格）' % dropped)

    if not os.path.exists(args.db):
        print('找不到数据库:', args.db); sys.exit(1)
    now = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
    backup = args.db + '.bak-' + datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S%f')
    with closing(sqlite3.connect(args.db)) as c:
        with closing(sqlite3.connect(backup)) as saved:
            c.backup(saved)
        with c:
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
            print('字数:', len(prompt))
            print('当前所有人格:', [r[0] for r in cur.execute('select persona_id from personas')])
    print('备份:', backup)


if __name__ == '__main__':
    main()
