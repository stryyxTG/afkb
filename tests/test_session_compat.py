import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tglol.session_compat import telethon_compatible_session_path


OLD_TELETHON_COLUMNS = ("dc_id", "server_address", "port", "auth_key", "takeout_id")
NEW_TELETHON_COLUMNS = ("dc_id", "server_address", "port", "auth_key", "tmp_auth_key", "takeout_id")


class SessionCompatTests(unittest.TestCase):
    def test_extra_tmp_auth_key_column_is_copied_to_old_telethon_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "account.session"
            connection = sqlite3.connect(str(session))
            try:
                connection.execute("CREATE TABLE version (version integer primary key)")
                connection.execute("INSERT INTO version VALUES (7)")
                connection.execute(
                    """
                    CREATE TABLE sessions (
                        dc_id INTEGER PRIMARY KEY,
                        server_address TEXT,
                        port INTEGER,
                        auth_key BLOB,
                        tmp_auth_key BLOB,
                        takeout_id INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (1, "149.154.175.55", 443, b"key", b"tmp", None),
                )
                connection.commit()
            finally:
                connection.close()

            with patch("tglol.session_compat.telethon_session_columns", return_value=OLD_TELETHON_COLUMNS):
                compat = telethon_compatible_session_path(session)

            self.assertNotEqual(compat, session)
            connection = sqlite3.connect(str(compat))
            try:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(sessions)")]
                row = connection.execute("SELECT * FROM sessions").fetchone()
            finally:
                connection.close()
            self.assertEqual(columns, list(OLD_TELETHON_COLUMNS))
            self.assertEqual(row, (1, "149.154.175.55", 443, b"key", None))

    def test_old_five_column_session_is_upgraded_in_place_for_new_telethon(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "login.session"
            connection = sqlite3.connect(str(session))
            try:
                connection.execute("CREATE TABLE version (version integer primary key)")
                connection.execute("INSERT INTO version VALUES (7)")
                connection.execute(
                    """
                    CREATE TABLE sessions (
                        dc_id INTEGER PRIMARY KEY,
                        server_address TEXT,
                        port INTEGER,
                        auth_key BLOB,
                        takeout_id INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                    (1, "149.154.175.55", 443, b"key", None),
                )
                connection.commit()
            finally:
                connection.close()

            with patch("tglol.session_compat.telethon_session_columns", return_value=NEW_TELETHON_COLUMNS):
                compat = telethon_compatible_session_path(session)

            self.assertEqual(compat, session)
            connection = sqlite3.connect(str(session))
            try:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(sessions)")]
                row = connection.execute("SELECT * FROM sessions").fetchone()
            finally:
                connection.close()
            self.assertEqual(columns, list(NEW_TELETHON_COLUMNS))
            self.assertEqual(row, (1, "149.154.175.55", 443, b"key", None, None))



    def test_matching_new_schema_syncs_old_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "already_new.session"
            connection = sqlite3.connect(str(session))
            try:
                connection.execute("CREATE TABLE version (version integer primary key)")
                connection.execute("INSERT INTO version VALUES (7)")
                connection.execute(
                    """
                    CREATE TABLE sessions (
                        dc_id INTEGER PRIMARY KEY,
                        server_address TEXT,
                        port INTEGER,
                        auth_key BLOB,
                        tmp_auth_key BLOB,
                        takeout_id INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (1, "149.154.175.55", 443, b"key", b"tmp", None),
                )
                connection.commit()
            finally:
                connection.close()

            with patch("tglol.session_compat.telethon_session_columns", return_value=NEW_TELETHON_COLUMNS):
                with patch("tglol.session_compat.CURRENT_VERSION", 8):
                    compat = telethon_compatible_session_path(session)

            self.assertEqual(compat, session)
            connection = sqlite3.connect(str(session))
            try:
                version = connection.execute("SELECT version FROM version").fetchone()[0]
                columns = [row[1] for row in connection.execute("PRAGMA table_info(sessions)")]
            finally:
                connection.close()
            self.assertEqual(version, 8)
            self.assertEqual(columns, list(NEW_TELETHON_COLUMNS))
if __name__ == "__main__":
    unittest.main()
