"""The registered SQLite aggregates (median / mean / mode) and that the run_sql
guardrail lets them through.
"""

from sqlalchemy import text

from agent.tools import _validate_select
from db import migrate
from db.engine import ro_engine, rw_engine


def _seed(values):
    migrate.main()
    with rw_engine().begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS t (v REAL)"))
        conn.execute(text("DELETE FROM t"))
        for v in values:
            conn.execute(text("INSERT INTO t (v) VALUES (:v)"), {"v": v})


def test_median_mean_mode(sqlite_env):
    _seed([1, 1, 2, 3, 10])
    with ro_engine().connect() as conn:
        row = conn.execute(text("SELECT median(v) me, mean(v) mn, mode(v) mo FROM t")).one()
    assert row.me == 2  # sorted [1,1,2,3,10] -> middle
    assert row.mn == 3.4  # (1+1+2+3+10)/5
    assert row.mo == 1  # most frequent


def test_aggregates_skip_nulls_and_handle_empty(sqlite_env):
    _seed([1, None, 3])
    with ro_engine().connect() as conn:
        (med,) = conn.execute(text("SELECT median(v) FROM t")).one()
        (empty,) = conn.execute(text("SELECT median(v) FROM t WHERE v > 100")).one()
    assert med == 2
    assert empty is None


def test_median_grouped(sqlite_env):
    migrate.main()
    with rw_engine().begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS g (k TEXT, v REAL)"))
        for k, v in [("a", 1), ("a", 3), ("a", 5), ("b", 10), ("b", 20)]:
            conn.execute(text("INSERT INTO g VALUES (:k, :v)"), {"k": k, "v": v})
    with ro_engine().connect() as conn:
        rows = dict(conn.execute(text("SELECT k, median(v) FROM g GROUP BY k")).all())
    assert rows == {"a": 3, "b": 15}


def test_guardrail_allows_registered_aggregates():
    # should not raise
    _validate_select("SELECT type, median(intensity) FROM x GROUP BY 1")
    _validate_select("SELECT mode(compliance_status), mean(site_eui) FROM energy_records")
