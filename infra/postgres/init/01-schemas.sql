-- Chạy một lần khi volume postgres-data còn trống.
-- Mỗi module sở hữu một schema riêng và tự quản lý bảng/migration của mình.
CREATE SCHEMA IF NOT EXISTS scope;      -- P1 Scope Management
CREATE SCHEMA IF NOT EXISTS auth;       -- P2 Auth Gateway
CREATE SCHEMA IF NOT EXISTS pipeline;   -- P3 Pipeline Orchestration
CREATE SCHEMA IF NOT EXISTS alerting;   -- P4 Alerting
CREATE SCHEMA IF NOT EXISTS dashboard;  -- P5 Dashboard
CREATE SCHEMA IF NOT EXISTS rules;      -- D2 Rule Management
CREATE SCHEMA IF NOT EXISTS detection;  -- D1/D3 kết quả phát hiện, điểm bất thường

COMMENT ON SCHEMA scope     IS 'P1 Scope Management';
COMMENT ON SCHEMA auth      IS 'P2 Auth Gateway';
COMMENT ON SCHEMA pipeline  IS 'P3 Pipeline Orchestration';
COMMENT ON SCHEMA alerting  IS 'P4 Alerting';
COMMENT ON SCHEMA dashboard IS 'P5 Dashboard';
COMMENT ON SCHEMA rules     IS 'D2 Rule Management';
COMMENT ON SCHEMA detection IS 'D1 Rule Engine / D3 Anomaly Scoring';
