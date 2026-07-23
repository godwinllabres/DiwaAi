"""Offline regression for the input-value hardening — run:
    python test_input_clamps.py

Covers, without importing the full app or loading models:
  • api/logger.py — row-limit clamps on every method reaching `LIMIT ?`
                    (SQLite reads a NEGATIVE limit as *unlimited*)
  • api/logger.py — retention `days` floor (days <= 0 put the cutoff in the
                    FUTURE, which deleted the entire chat + feedback history)
  • api/logger.py — user_id allowlist on the export filename (path traversal)
See HANDOFF.md (P1/P2), docs/privacy_compliance.md §2.
"""
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from api.logger import ChatLogger, _clamp_limit, is_safe_user_id

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        got={got!r}\n        want={want!r}"
          if not ok else f"PASS  {name}")


def check_true(name, cond):
    global failures
    if not cond:
        failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# ── _clamp_limit ─────────────────────────────────────────────────────────────
print("── limit clamp ──")
check("in-range passes through", _clamp_limit(25, 50, 200), 25)
check("negative floors to 1 (was: unlimited)", _clamp_limit(-1, 50, 200), 1)
check("zero floors to 1", _clamp_limit(0, 50, 200), 1)
check("over cap truncates", _clamp_limit(10_000, 50, 200), 200)
check("cap is inclusive", _clamp_limit(200, 50, 200), 200)
check("numeric string coerces", _clamp_limit("30", 50, 200), 30)
check("garbage falls back to default", _clamp_limit("all", 50, 200), 50)
check("None falls back to default", _clamp_limit(None, 50, 200), 50)
check("float truncates toward zero", _clamp_limit(12.9, 50, 200), 12)


# ── user_id allowlist (export filename) ──────────────────────────────────────
print("\n── user_id allowlist ──")
check_true("plain id allowed", is_safe_user_id("student_202012345"))
check_true("dots and dashes allowed", is_safe_user_id("juan.dela-cruz"))
check_true("posix traversal rejected", not is_safe_user_id("../../etc/passwd"))
check_true("windows traversal rejected", not is_safe_user_id(r"..\..\www\index"))
check_true("decoded backslash rejected", not is_safe_user_id("a\\b"))
check_true("forward slash rejected", not is_safe_user_id("a/b"))
check_true("bare .. rejected", not is_safe_user_id(".."))
check_true("null byte rejected", not is_safe_user_id("ok\x00.json"))
check_true("empty rejected", not is_safe_user_id(""))
check_true("over-long rejected", not is_safe_user_id("x" * 65))
check_true("non-str rejected", not is_safe_user_id(None))


# ── against a real (temporary) SQLite database ───────────────────────────────
print("\n── logger behaviour (temp sqlite) ──")
tmp = Path(tempfile.mkdtemp(prefix="sevi_clamps_"))
logger = ChatLogger(log_dir=str(tmp), db_path=str(tmp / "chat_history.db"))

for i in range(5):
    logger.log_chat(
        user_id="u1", user_message=f"q{i}", bot_response=f"a{i}",
        intent="nlu_fallback", confidence=0.2, session_id="s1",
    )

# limit=-1 used to mean "every row in the table"; it must now mean one row.
check("get_user_history(-1) returns 1 row", len(logger.get_user_history("u1", -1)), 1)
check("get_recent_messages(-1) returns 1 row", len(logger.get_recent_messages(-1)), 1)
check("get_session_list(-1) returns 1 row", len(logger.get_session_list("u1", -1)), 1)
check("search_logs(-1) returns 1 row", len(logger.search_logs("q", -1)), 1)
check("get_fallback_examples(-1) returns 1 row", len(logger.get_fallback_examples(-1)), 1)
check("get_anti_pattern_rows(-1) returns 1 row",
      len(logger.get_anti_pattern_rows(days=30, limit=-1)), 1)
check("get_feedback_entries(-1) does not error", logger.get_feedback_entries(limit=-1), [])
# The normal path is untouched.
check("get_user_history(50) still returns all 5", len(logger.get_user_history("u1", 50)), 5)

# Retention: a non-positive window must NOT delete rows written seconds ago.
check("cleanup_old_logs(0) deletes nothing", logger.cleanup_old_logs(0), 0)
check("cleanup_old_logs(-9999) deletes nothing", logger.cleanup_old_logs(-9999), 0)
check("rows survived the bad windows", len(logger.get_user_history("u1", 50)), 5)
# ...and a legitimate window still works.
check("cleanup_old_logs(1) spares fresh rows", logger.cleanup_old_logs(1), 0)

# Export: traversal ids refused, safe ids land inside log_dir.
check("export refuses posix traversal", logger.export_user_data("../../evil"), None)
check("export refuses windows traversal", logger.export_user_data(r"..\..\evil"), None)
exported = logger.export_user_data("u1")
check_true("export writes for a safe id", exported is not None)
check_true("export stays inside log_dir",
           exported is not None and Path(exported).parent.resolve() == tmp.resolve())

# A stale row (older than the window) IS still purged — the guard must not
# have turned retention into a no-op.
old_iso = (datetime.now() - timedelta(days=400)).isoformat()
conn, cursor = logger._connect()
cursor.execute("UPDATE chat_messages SET timestamp = ? WHERE user_id = 'u1'", (old_iso,))
conn.commit()
conn.close()
check("cleanup_old_logs(30) still purges stale rows", logger.cleanup_old_logs(30), 5)

shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
