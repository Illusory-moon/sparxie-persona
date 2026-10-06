import glob
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing


class MakePersonaTest(unittest.TestCase):
    def test_update_can_be_restored_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "personas.db")
            md = os.path.join(tmp, "persona.md")
            with closing(sqlite3.connect(db)) as con:
                with con:
                    con.execute("CREATE TABLE personas (persona_id TEXT, system_prompt TEXT, "
                                "begin_dialogs TEXT, tools TEXT, skills TEXT, "
                                "created_at TEXT, updated_at TEXT, sort_order INT)")
                    con.execute("INSERT INTO personas (persona_id, system_prompt) VALUES (?, ?)",
                                ("test", "old"))
            with open(md, "w", encoding="utf-8") as source:
                source.write("new")
            subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "make-persona.py"),
                            "--db", db, "--md", md, "--name", "test"],
                           check=True, capture_output=True)
            backups = glob.glob(db + ".bak-*")
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("SELECT system_prompt FROM personas").fetchone()[0], "new")
            with closing(sqlite3.connect(backups[0])) as con:
                self.assertEqual(con.execute("SELECT system_prompt FROM personas").fetchone()[0], "old")


if __name__ == "__main__":
    unittest.main()
