#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人格写进 AstrBot 的 personas 表

用法:
    python make-persona.py --db /opt/astrbot/data/data_v4.db [--name sparxie] [--md persona/sparxie-qq.md]

写入前会先检查两件事（2026-10-12 加）：
  1. 库里那份与本次要写的**完全相同** → 直接返回：不写库、不产生备份
     （旧版每次同步都全量复制一份库，而 data_v4.db 已经是十几 MB 级）
  2. 本 md 的修改时间**明显早于**库里上次更新时间 → 判定「拿旧副本同步」，
     打印警告并**中止**（退出码 2）；确认无误时加 `--force`

为什么加这个护栏：2026-10-12 在开发者的服务器上实测到一次**部署漂移** ——
  `/opt/mindscape/sparxie-qq.md` 停在 10-05，而仓库的 `persona/sparxie-qq.md`
  已经是 10-08（中间三次提交：开场白的管辖范围、同行改事件态、三处勘误）。
  当时库里那份是新的 ✓，但**只要有人照 README 在服务器上跑一次本脚本**、
  且 `--md` 指向那份旧副本，线上人格就会**静默倒退 3 天** —— 全程没有任何提示 ✗。
  护栏放在这里最省事：它正好挡在那个危险动作前面。
"""
import argparse
import datetime
import difflib
import os
import re
import sqlite3
import sys
from contextlib import closing

# 「我的成长记录（自动写入）」—— soul_memory 每次自动往 SOUL.md 追加的那一段 ✓。
# 它是**记忆** ✗ 不是人格正文 ✓（而 SOUL.md 本身还挂在 extra_diaries 里 ✓ 记忆通道已经覆盖它 ✓）。
# 2026-10-08 开发者裁定 ✓：同步人格时**一律剔掉** ✗ —— 否则每同步一次，就把越攒越陈的记忆塞进人格 ✓（留尾巴 ✗）。
AUTO_SECTION = re.compile(r'^#{0,3}\s*\**\s*我的成长记录', re.M)

# md 比库旧多少才判定「你拿的是旧副本」：留 60 秒容差，免得同一次操作里的先后顺序误伤 ✓
STALE_TOLERANCE_SEC = 60


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


def db_updated_ts(value):
    """把库里的 updated_at（naive UTC 字符串）换成时间戳；解析不了返回 None ✓（解析不了就不拦）✓"""
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.datetime.strptime(str(value), fmt).replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def diff_stat(old, new):
    """只数「加了几行 / 减了几行」✓，不打印正文（人格正文不进日志）✓"""
    d = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=0))
    add = sum(1 for x in d if x.startswith('+') and not x.startswith('+++'))
    rm = sum(1 for x in d if x.startswith('-') and not x.startswith('---'))
    return add, rm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='AstrBot 的 data_v4.db 路径')
    ap.add_argument('--name', default='sparxie', help='人格 id（默认 sparxie）')
    ap.add_argument('--md', default=None, help='人格 markdown 路径')
    ap.add_argument('--force', action='store_true', help='跳过「拿旧副本同步」的护栏')
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

    with closing(sqlite3.connect(args.db)) as c:
        cur = c.cursor()
        row = cur.execute('select system_prompt, updated_at from personas where persona_id=?',
                          (args.name,)).fetchone()
        old = (row[0] or '') if row else None
        old_at = str(row[1]) if (row and row[1]) else ''

        if old is not None and old == prompt:
            print('库里已是这一版，无需更新 ✓（未写库、未产生备份）')
            print('字数:', len(prompt))
            return

        if old is not None:
            add, rm = diff_stat(old, prompt)
            print('本次改动: +%d 行 / -%d 行' % (add, rm))
            if old_at:
                print('库里上次更新: %s (UTC)' % old_at)
            db_ts = db_updated_ts(old_at)
            md_ts = os.path.getmtime(md)
            if db_ts is not None and md_ts < db_ts - STALE_TOLERANCE_SEC and not args.force:
                print()
                print('🔴 中止：这份 md 的修改时间（%s）早于库里那次更新（%s UTC）'
                      % (datetime.datetime.fromtimestamp(md_ts).strftime('%Y-%m-%d %H:%M:%S'), old_at))
                print('   md: %s' % md)
                print('   看起来手上是**旧副本** —— 照这样同步会把线上人格往后退 ✗。')
                print('   先确认改的是最新那份（仓库里 git log 一下这套文件）；')
                print('   确认就是要回退，再加 --force 重跑 ✓。')
                sys.exit(2)

        now = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S.%f')
        backup = args.db + '.bak-' + datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S%f')
        with closing(sqlite3.connect(backup)) as saved:
            c.backup(saved)
        with c:
            if old is not None:
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
