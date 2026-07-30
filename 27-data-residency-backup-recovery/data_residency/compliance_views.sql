-- =============================================================================
-- REGULATORY REQUIREMENT: Multi-jurisdiction — Data Residency & Backup Compliance
-- Regulation:  NJ N.J.A.C. 13:69O-1.4(k) — system availability and disaster recovery;
--              MGA Remote Gaming Regulations Art. 10 — technical system requirements;
--              UKGC LCCP Technical Standards — system resilience;
--              GDPR Art. 32 — appropriate technical measures including availability;
--              Brazil LGPD Art. 46 — technical security measures;
--              Ontario AGCO Registrar's Standards — cybersecurity (updated early 2025)
-- Purpose:     Monthly backup compliance dashboard and daily automated failure alerts.
--              Demonstrates to regulators that backup processes are functioning,
--              data is recoverable, and jurisdictional data residency requirements
--              are being met (e.g., NJ data must remain in the US; MGA Malta data
--              may not leave the EEA without Standard Contractual Clauses).
-- Data Residency Requirements by Jurisdiction:
--   NJ DGE    — Primary and disaster recovery servers must be in the US
--   MGA Malta — Data in EEA (GDPR Art. 44-49); transfers outside EEA require SCCs
--   UKGC      — No specific residency mandate, but data transfer rules apply
--   Brazil    — LGPD Art. 33: transfers only to countries with adequate protection
--              or with specific safeguards (ANPD adequacy list, 2024)
--   Ontario   — No specific residency law but PIPEDA cross-border transfer rules
-- Backup Retention: Align with the longest applicable retention period (10 years
--              for NJ DGE; 7 years for UK MLR; use 10 years as the safe default)
-- Penalty:     MGA: licence suspension for data unavailability; GDPR Art. 83(4):
--              up to €10M or 2% for Art. 32 (security) failures
-- Last Verified: March 2026
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   GDPR Full Text: https://gdpr-info.eu/
--   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
-- =============================================================================
-- Compliance Views - Chapter 24: Data Residency and Backup/Recovery Strategy
--
-- SQL views and pg_cron job for backup compliance reporting.
-- Provides monthly backup compliance metrics and daily automated alerts
-- for backup failure rates exceeding thresholds.
--
-- Part of the iGaming Platform Engineering book.
--
-- Note: CREATE EVENT is MySQL-specific syntax. In PostgreSQL, use pg_cron
-- or an external scheduler (pg_cron example commented out below).

-- Monthly backup compliance report
CREATE OR REPLACE VIEW monthly_backup_compliance AS
SELECT
    jurisdiction,
    backup_type,
    COUNT(*) as total_backups,
    AVG(duration_minutes) as avg_duration,
    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as successful_backups,
    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_backups,
    SUM(data_size_gb) as total_data_gb,
    AVG(compression_ratio) as avg_compression_ratio,
    SUM(encryption_verification_count) as encryption_checks,
    COUNT(CASE WHEN jurisdiction_compliance = 'PASSED' THEN 1 END) as compliant_backups
FROM backup_logs
WHERE backup_date >= NOW() - INTERVAL '30 days'
GROUP BY jurisdiction, backup_type
ORDER BY jurisdiction, backup_type;

-- Automated compliance check function (replaces MySQL EVENT)
-- In production, call this via pg_cron: SELECT cron.schedule('daily-backup-alert', '0 6 * * *', 'SELECT check_backup_compliance();');
CREATE OR REPLACE FUNCTION check_backup_compliance() RETURNS VOID AS $$
DECLARE
    failed_backups INT;
    total_backups_count INT;
BEGIN
    SELECT
        SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END),
        COUNT(*)
    INTO failed_backups, total_backups_count
    FROM backup_logs
    WHERE backup_date >= NOW() - INTERVAL '1 day';

    IF failed_backups > 0 OR (total_backups_count > 0 AND failed_backups::FLOAT / total_backups_count > 0.05) THEN
        INSERT INTO compliance_alerts (alert_type, severity, message, created_at)
        VALUES (
            'BACKUP_FAILURE',
            'CRITICAL',
            'Backup failures detected: ' || failed_backups || ' out of ' || total_backups_count,
            NOW()
        );
    END IF;
END;
$$ LANGUAGE plpgsql;
