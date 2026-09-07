-- One-time local bootstrap. Run once as an admin/root MySQL user:
--
--   "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p < db/bootstrap.sql
--
-- or open this file in MySQL Workbench (already installed) and execute it.
--
-- Creates the benchmark database and the two least-privilege users from the
-- architecture doc section 4.3. CHANGE THE TWO PASSWORDS BELOW first, then put
-- the same values in .env (DB_ETL_USER/DB_ETL_PASSWORD, DB_AGENT_USER/
-- DB_AGENT_PASSWORD). .env is gitignored.

CREATE DATABASE IF NOT EXISTS benchmark
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- ETL user: full rights on the benchmark schema. Used by ingest/ and migrations,
-- nothing else.
CREATE USER IF NOT EXISTS 'benchmark_etl'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_etl';
GRANT ALL PRIVILEGES ON benchmark.* TO 'benchmark_etl'@'localhost';

-- Agent user: SELECT and nothing else. This is the core security control — the
-- agent physically cannot write, drop, or alter (architecture doc section 8.1).
--
-- Note: MySQL has no per-user statement-time cap (that is a MariaDB feature).
-- The run_sql tool enforces SQL_TIMEOUT_MS per connection via
-- `SET SESSION max_execution_time` (implemented at M4). MAX_USER_CONNECTIONS is
-- a mild backstop only.
CREATE USER IF NOT EXISTS 'benchmark_agent'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_agent'
  WITH MAX_USER_CONNECTIONS 10;
GRANT SELECT ON benchmark.* TO 'benchmark_agent'@'localhost';

FLUSH PRIVILEGES;

SELECT 'bootstrap complete'                       AS status,
       (SELECT COUNT(*) FROM mysql.user
         WHERE user IN ('benchmark_etl','benchmark_agent')) AS users_created;
