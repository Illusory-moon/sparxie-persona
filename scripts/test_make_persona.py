import datetime
import glob
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "make-persona.py")
SCHEMA = ("CREATE TABLE personas (persona_id TEXT, system_prompt TEXT, "
          "begin_dialogs TEXT, tools TEXT, skills TEXT, "
          "created_at TEXT, updated_at TEXT, sort_order INT)")


def run(args, env=None):
    """跑一次脚本，固定按 UTF-8 收输出（Windows 上子进程默认是本地码页）✓"""
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, env=e)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def make_db(path, rows=()):
    with closing(sqlite3.connect(path)) as con:
        with con:
            con.execute(SCHEMA)
            for pid, prompt, updated in rows:
                con.execute("INSERT INTO personas (persona_id, system_prompt, updated_at)"
                            " VALUES (?,?,?)", (pid, prompt, updated))


def prompt_of(path, pid):
    with closing(sqlite3.connect(path)) as con:
        return con.execute("SELECT system_prompt FROM personas WHERE persona_id=?",
                           (pid,)).fetchone()[0]


def utc(delta_sec=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=delta_sec)).strftime("%Y-%m-%d %H:%M:%S.%f")


class MakePersonaTest(unittest.TestCase):
    def test_update_can_be_restored_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "personas.db")
            md = os.path.join(tmp, "persona.md")
            make_db(db, [("test", "old", None)])
            with open(md, "w", encoding="utf-8") as source:
                source.write("new")
            code, out, err = run(["--db", db, "--md", md, "--name", "test"])
            self.assertEqual(code, 0, err)
            backups = glob.glob(db + ".bak-*")
            self.assertEqual(len(backups), 1)
            self.assertEqual(prompt_of(db, "test"), "new")
            with closing(sqlite3.connect(backups[0])) as con:
                self.assertEqual(con.execute("SELECT system_prompt FROM personas").fetchone()[0], "old")

    def test_auto_memory_section_is_stripped(self):
        """自动成长段是**记忆** ✗ 不许进人格正文 ✓（2026-10-08 开发者裁定 ✓）。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "p.db")
            md = os.path.join(tmp, "p.md")
            make_db(db)
            with open(md, "w", encoding="utf-8") as f:
                f.write("人格正文在这里\n\n## 我的成长记录（自动写入）\n- 记忆甲\n- 记忆乙\n")
            code, out, err = run(["--db", db, "--md", md, "--name", "t"])
            self.assertEqual(code, 0, err)
            got = prompt_of(db, "t")
            self.assertIn("人格正文在这里", got)      # 正文留着 ✓
            self.assertNotIn("我的成长记录", got)     # 成长段剔掉 ✓
            self.assertNotIn("记忆甲", got)

    def test_same_content_skips_write_and_backup(self):
        """库里已是这一版 → 不写库、**不产生备份** ✓

        旧版每同步一次就全量复制一份 data_v4.db（线上那份已经是十几 MB 级），
        反复跑等于往磁盘上堆垃圾 ✗。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "p.db")
            md = os.path.join(tmp, "p.md")
            body = "同一版人格\n第二行\n"
            make_db(db, [("t", body, utc(-3600))])
            with open(md, "w", encoding="utf-8") as f:
                f.write(body)
            code, out, err = run(["--db", db, "--md", md, "--name", "t"])
            self.assertEqual(code, 0, err)
            self.assertIn("无需更新", out)
            self.assertEqual(glob.glob(db + ".bak-*"), [])          # 没备份 ✓
            self.assertEqual(prompt_of(db, "t"), body)              # 库也没动 ✓

    def test_stale_md_is_refused_then_force_works(self):
        """md 比「库里那次更新」还旧 → 判定旧副本、中止（退出码 2），库不动 ✓；--force 可越过 ✓

        2026-10-12 实测的部署漂移：服务器上那份 md 停在 10-05，仓库已是 10-08。
        库里当时是新的，但只要有人拿旧副本同步一次，线上人格就静默倒退三天 ✗。
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "p.db")
            md = os.path.join(tmp, "p.md")
            live = "线上这一版\n"
            make_db(db, [("t", live, utc(-600))])          # 库里 10 分钟前更新过
            with open(md, "w", encoding="utf-8") as f:
                f.write("旧副本的内容\n")
            stale = (datetime.datetime.now() - datetime.timedelta(hours=3)).timestamp()
            os.utime(md, (stale, stale))                    # md 三小时没动过 → 比库里那次更新还早

            code, out, err = run(["--db", db, "--md", md, "--name", "t"])
            self.assertEqual(code, 2, out)                  # 中止 ✓
            self.assertIn("旧副本", out)
            self.assertEqual(prompt_of(db, "t"), live)      # 库一个字没动 ✓
            self.assertEqual(glob.glob(db + ".bak-*"), [])

            code, out, err = run(["--db", db, "--md", md, "--name", "t", "--force"])
            self.assertEqual(code, 0, err)                  # 确认无误 → 放行 ✓
            self.assertEqual(prompt_of(db, "t"), "旧副本的内容\n")

    def test_fresh_md_is_not_blocked(self):
        """md 比库新 → 正常写入 ✓（护栏不许误伤正常流程）"""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "p.db")
            md = os.path.join(tmp, "p.md")
            make_db(db, [("t", "旧内容\n", utc(-3600))])
            with open(md, "w", encoding="utf-8") as f:
                f.write("新内容\n")                          # mtime = 现在 → 比库里那次更新新 ✓
            code, out, err = run(["--db", db, "--md", md, "--name", "t"])
            self.assertEqual(code, 0, err)
            self.assertIn("已更新人格", out)
            self.assertEqual(prompt_of(db, "t"), "新内容\n")


if __name__ == "__main__":
    unittest.main()
