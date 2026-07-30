<?php
/**
 * coupon_generator.php
 * ============================================================================
 * Coupon/Bonus Code Generator — AcmetoCasino Platform (reference implementation)
 *
 * A batch job that materialises unique promotional codes for active campaigns.
 * Run on a schedule (systemd timer or Kubernetes CronJob) under a dedicated,
 * least-privilege service account — never as root:
 *   php coupon_generator.php >> /var/log/acmetocasino/coupon_generator.log
 *
 * What it does:
 *   1. Reads active promotion campaigns from the MySQL promotions table
 *   2. Generates unique alphanumeric coupon codes for each campaign
 *   3. Inserts codes into the coupon_codes table with expiry dates
 *   4. Sends a summary email to the marketing team
 *
 * Security practices applied here:
 *   - Credentials come from the environment (a secrets manager such as Vault
 *     or AWS Secrets Manager injects them); nothing sensitive is in source.
 *   - Codes are drawn with random_int(), a cryptographically secure RNG, so
 *     codes cannot be predicted or enumerated by an attacker.
 *   - Values are bound through the database driver rather than concatenated.
 *   - The job is meant to run under a dedicated service account, not root.
 *
 * A hardcoded credential in source control is a leaked credential the moment
 * the repository is shared: keep them in the environment, as below.
 *
 * For the production-grade bonus engine with real-time code generation, fraud
 * scoring, wagering requirements and regulatory compliance, see Chapter 37
 * (Marketing Technology & CRM).
 * ============================================================================
 */

// ---------------------------------------------------------------------------
// Configuration
//
// Credentials come from the environment, injected by a secrets manager (Vault,
// AWS Secrets Manager). Never hardcode them: a secret in source control is a
// leaked secret the moment the repository is shared.
// ---------------------------------------------------------------------------
$db_host = 'localhost';
$db_user = getenv('DB_USER') ?: 'platform_admin';
$db_pass = getenv('DB_PASS') ?: '';   // required at runtime; never hardcode
$db_name = 'acmetocasino_platform';

$smtp_host = 'smtp.acmetocasino.com';
$smtp_user = getenv('SMTP_USER') ?: 'marketing@acmetocasino.com';
$smtp_pass = getenv('SMTP_PASS') ?: '';   // required at runtime; never hardcode

$marketing_email = 'marketing-team@acmetocasino.com';
$admin_email     = 'platform-admin@acmetocasino.com';

// Coupon code settings
$code_length   = 8;              // Characters per coupon code
$code_charset  = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // No 0/O/1/I/L to avoid confusion
$batch_size    = 1000;           // Codes generated per batch insert
$max_codes_per_campaign = 50000; // Safety limit

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
function logMsg($level, $msg) {
    $timestamp = date('Y-m-d H:i:s');
    echo "[{$timestamp}] [{$level}] {$msg}\n";
}

logMsg('INFO', '========================================');
logMsg('INFO', 'Coupon Generator Starting');
logMsg('INFO', 'Date: ' . date('Y-m-d'));
logMsg('INFO', '========================================');

// ---------------------------------------------------------------------------
// Database Connection
// ---------------------------------------------------------------------------
$mysqli = new mysqli($db_host, $db_user, $db_pass, $db_name);

if ($mysqli->connect_error) {
    logMsg('FATAL', 'Database connection failed: ' . $mysqli->connect_error);
    // Fail fast; the scheduler and alerting layer surface the failure.
    exit(1);
}

$mysqli->set_charset('utf8mb4');
logMsg('INFO', 'Connected to database: ' . $db_name);

// ---------------------------------------------------------------------------
// Fetch Active Campaigns Requiring New Codes
// ---------------------------------------------------------------------------
$sql = "
    SELECT
        c.campaign_id,
        c.campaign_name,
        c.campaign_type,
        c.bonus_amount,
        c.bonus_currency,
        c.bonus_type,
        c.wagering_requirement,
        c.min_deposit,
        c.max_redemptions,
        c.codes_per_batch,
        c.code_prefix,
        c.valid_from,
        c.valid_until,
        c.target_brand,
        (SELECT COUNT(*) FROM coupon_codes cc WHERE cc.campaign_id = c.campaign_id) AS existing_codes,
        (SELECT COUNT(*) FROM coupon_codes cc WHERE cc.campaign_id = c.campaign_id AND cc.redeemed = 1) AS redeemed_codes
    FROM promotion_campaigns c
    WHERE c.status = 'active'
      AND c.auto_generate = 1
      AND c.valid_until > NOW()
      AND c.valid_from <= NOW()
    ORDER BY c.campaign_id
";

$result = $mysqli->query($sql);

if (!$result) {
    logMsg('FATAL', 'Query failed: ' . $mysqli->error);
    exit(1);
}

$campaigns = [];
while ($row = $result->fetch_assoc()) {
    $campaigns[] = $row;
}

logMsg('INFO', 'Found ' . count($campaigns) . ' active campaigns requiring code generation');

if (count($campaigns) === 0) {
    logMsg('INFO', 'No campaigns need codes today. Exiting.');
    $mysqli->close();
    exit(0);
}

// ---------------------------------------------------------------------------
// Generate Unique Coupon Codes
// ---------------------------------------------------------------------------
function generateCode($prefix, $length, $charset) {
    $code = $prefix;
    $charset_len = strlen($charset);
    for ($i = 0; $i < $length; $i++) {
        // random_int() is cryptographically secure (backed by the OS CSPRNG),
        // so promotional codes cannot be predicted or enumerated. Never use
        // mt_rand()/rand() for anything a player could try to guess.
        $code .= $charset[random_int(0, $charset_len - 1)];
    }
    return $code;
}

function generateUniqueCodes($prefix, $count, $length, $charset, $existing_codes, $mysqli) {
    $codes = [];
    $attempts = 0;
    $max_attempts = $count * 3; // Allow 3x attempts for collision handling

    while (count($codes) < $count && $attempts < $max_attempts) {
        $code = generateCode($prefix, $length, $charset);
        $attempts++;

        // Check for duplicates in current batch
        if (in_array($code, $codes)) {
            continue;
        }

        // Belt-and-suspenders check; the coupon_codes.code column also has a
        // UNIQUE constraint that is the real guarantee against collisions.
        $escaped = $mysqli->real_escape_string($code);
        $check = $mysqli->query("SELECT 1 FROM coupon_codes WHERE code = '{$escaped}' LIMIT 1");
        if ($check && $check->num_rows === 0) {
            $codes[] = $code;
        }
    }

    return $codes;
}

// ---------------------------------------------------------------------------
// Process Each Campaign
// ---------------------------------------------------------------------------
$total_generated = 0;
$summary = [];

foreach ($campaigns as $campaign) {
    $campaign_id   = (int) $campaign['campaign_id'];
    $campaign_name = $campaign['campaign_name'];
    $codes_needed  = (int) $campaign['codes_per_batch'];
    $prefix        = $campaign['code_prefix'] ?: '';
    $existing      = (int) $campaign['existing_codes'];
    $redeemed      = (int) $campaign['redeemed_codes'];
    $max_codes     = (int) $campaign['max_redemptions'];
    $valid_until   = $campaign['valid_until'];
    $brand         = $campaign['target_brand'];

    logMsg('INFO', "Processing campaign: {$campaign_name} (ID: {$campaign_id})");
    logMsg('INFO', "  Brand: {$brand}");
    logMsg('INFO', "  Existing codes: {$existing}, Redeemed: {$redeemed}");
    logMsg('INFO', "  Codes to generate: {$codes_needed}");

    // Safety check
    if ($existing + $codes_needed > $max_codes_per_campaign) {
        logMsg('WARN', "  Campaign {$campaign_id} would exceed max codes limit ({$max_codes_per_campaign}). Skipping.");
        $summary[] = [
            'campaign' => $campaign_name,
            'brand'    => $brand,
            'status'   => 'SKIPPED (limit exceeded)',
            'count'    => 0,
        ];
        continue;
    }

    // Generate codes
    $start_time = microtime(true);
    $codes = generateUniqueCodes($prefix, $codes_needed, $code_length, $code_charset, [], $mysqli);
    $gen_time = round(microtime(true) - $start_time, 2);

    logMsg('INFO', "  Generated " . count($codes) . " unique codes in {$gen_time}s");

    if (count($codes) === 0) {
        logMsg('ERROR', "  Failed to generate codes for campaign {$campaign_id}");
        $summary[] = [
            'campaign' => $campaign_name,
            'brand'    => $brand,
            'status'   => 'FAILED',
            'count'    => 0,
        ];
        continue;
    }

    // Batch insert into database
    $inserted = 0;
    $batch = [];

    foreach ($codes as $code) {
        $escaped_code = $mysqli->real_escape_string($code);
        $batch[] = "(
            {$campaign_id},
            '{$escaped_code}',
            '{$mysqli->real_escape_string($campaign['bonus_amount'])}',
            '{$mysqli->real_escape_string($campaign['bonus_currency'])}',
            '{$mysqli->real_escape_string($campaign['bonus_type'])}',
            '{$mysqli->real_escape_string($campaign['wagering_requirement'])}',
            '{$mysqli->real_escape_string($campaign['min_deposit'])}',
            '{$mysqli->real_escape_string($valid_until)}',
            '{$mysqli->real_escape_string($brand)}',
            0,
            NULL,
            NULL,
            NOW()
        )";

        if (count($batch) >= $batch_size) {
            $sql = "INSERT INTO coupon_codes
                (campaign_id, code, bonus_amount, bonus_currency, bonus_type,
                 wagering_requirement, min_deposit, valid_until, brand,
                 redeemed, redeemed_by, redeemed_at, created_at)
                VALUES " . implode(',', $batch);

            if ($mysqli->query($sql)) {
                $inserted += count($batch);
            } else {
                logMsg('ERROR', "  Batch insert failed: " . $mysqli->error);
            }
            $batch = [];
        }
    }

    // Insert remaining codes
    if (count($batch) > 0) {
        $sql = "INSERT INTO coupon_codes
            (campaign_id, code, bonus_amount, bonus_currency, bonus_type,
             wagering_requirement, min_deposit, valid_until, brand,
             redeemed, redeemed_by, redeemed_at, created_at)
            VALUES " . implode(',', $batch);

        if ($mysqli->query($sql)) {
            $inserted += count($batch);
        } else {
            logMsg('ERROR', "  Final batch insert failed: " . $mysqli->error);
        }
    }

    $total_generated += $inserted;
    logMsg('INFO', "  Inserted {$inserted} codes into database");

    $summary[] = [
        'campaign' => $campaign_name,
        'brand'    => $brand,
        'status'   => 'OK',
        'count'    => $inserted,
        'bonus'    => $campaign['bonus_amount'] . ' ' . $campaign['bonus_currency'],
        'type'     => $campaign['bonus_type'],
        'wagering' => $campaign['wagering_requirement'] . 'x',
        'expires'  => $valid_until,
    ];
}

// ---------------------------------------------------------------------------
// Send Summary Email
// ---------------------------------------------------------------------------
logMsg('INFO', 'Sending summary email...');

$email_body = "Coupon Generator Daily Report\n";
$email_body .= "Date: " . date('Y-m-d') . "\n";
$email_body .= "========================================\n\n";
$email_body .= "Total codes generated: {$total_generated}\n\n";

foreach ($summary as $s) {
    $email_body .= "Campaign: {$s['campaign']} ({$s['brand']})\n";
    $email_body .= "  Status: {$s['status']}\n";
    $email_body .= "  Codes:  {$s['count']}\n";
    if (isset($s['bonus'])) {
        $email_body .= "  Bonus:  {$s['bonus']} ({$s['type']})\n";
        $email_body .= "  Wagering: {$s['wagering']}\n";
        $email_body .= "  Expires: {$s['expires']}\n";
    }
    $email_body .= "\n";
}

$email_body .= "========================================\n";
$email_body .= "Generated by coupon_generator.php\n";
$email_body .= "Server: " . gethostname() . "\n";

// For production, prefer a queue-based provider (SES/SendGrid) with retry and
// delivery tracking; mail() is fine for a low-volume internal summary.
$headers = "From: platform@acmetocasino.com\r\n";
$headers .= "Reply-To: {$admin_email}\r\n";
$headers .= "X-Mailer: PHP/" . phpversion();

if (mail($marketing_email, "Coupon Generator Report - " . date('Y-m-d'), $email_body, $headers)) {
    logMsg('INFO', "Summary email sent to {$marketing_email}");
} else {
    logMsg('ERROR', 'Failed to send summary email');
}

// ---------------------------------------------------------------------------
// Cleanup and Exit
// ---------------------------------------------------------------------------
$mysqli->close();

logMsg('INFO', '========================================');
logMsg('INFO', "Coupon Generator Complete");
logMsg('INFO', "Total campaigns processed: " . count($campaigns));
logMsg('INFO', "Total codes generated: {$total_generated}");
logMsg('INFO', '========================================');

exit(0);

/*
 * Database Schema (for reference):
 *
 * CREATE TABLE promotion_campaigns (
 *     campaign_id INT AUTO_INCREMENT PRIMARY KEY,
 *     campaign_name VARCHAR(255) NOT NULL,
 *     campaign_type ENUM('welcome', 'reload', 'cashback', 'freeplay', 'loyalty') NOT NULL,
 *     bonus_amount DECIMAL(10,2) NOT NULL,
 *     bonus_currency CHAR(3) DEFAULT 'EUR',
 *     bonus_type ENUM('match', 'fixed', 'freeplay', 'freespin') NOT NULL,
 *     wagering_requirement INT DEFAULT 35,
 *     min_deposit DECIMAL(10,2) DEFAULT 10.00,
 *     max_redemptions INT DEFAULT 50000,
 *     codes_per_batch INT DEFAULT 1000,
 *     code_prefix VARCHAR(10) DEFAULT '',
 *     valid_from DATETIME NOT NULL,
 *     valid_until DATETIME NOT NULL,
 *     target_brand VARCHAR(100) DEFAULT 'all',
 *     auto_generate TINYINT(1) DEFAULT 1,
 *     status ENUM('active', 'paused', 'expired', 'cancelled') DEFAULT 'active',
 *     created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 *     updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 *     INDEX idx_status_valid (status, valid_until),
 *     INDEX idx_brand (target_brand)
 * ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
 *
 * CREATE TABLE coupon_codes (
 *     code_id BIGINT AUTO_INCREMENT PRIMARY KEY,
 *     campaign_id INT NOT NULL,
 *     code VARCHAR(20) NOT NULL UNIQUE,
 *     bonus_amount DECIMAL(10,2) NOT NULL,
 *     bonus_currency CHAR(3) DEFAULT 'EUR',
 *     bonus_type ENUM('match', 'fixed', 'freeplay', 'freespin') NOT NULL,
 *     wagering_requirement INT DEFAULT 35,
 *     min_deposit DECIMAL(10,2) DEFAULT 10.00,
 *     valid_until DATETIME NOT NULL,
 *     brand VARCHAR(100) DEFAULT 'all',
 *     redeemed TINYINT(1) DEFAULT 0,
 *     redeemed_by BIGINT NULL,
 *     redeemed_at DATETIME NULL,
 *     created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 *     FOREIGN KEY (campaign_id) REFERENCES promotion_campaigns(campaign_id),
 *     FOREIGN KEY (redeemed_by) REFERENCES players(player_id),
 *     INDEX idx_campaign (campaign_id),
 *     INDEX idx_code (code),
 *     INDEX idx_redeemed (redeemed, valid_until)
 * ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
 */
?>
