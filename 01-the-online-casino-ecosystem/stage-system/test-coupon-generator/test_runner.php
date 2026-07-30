<?php
/**
 * test_runner.php — Integration test for coupon_generator.php
 * Runs inside Docker container, validates the coupon generator works correctly.
 */

echo "============================================\n";
echo "Coupon Generator Integration Test\n";
echo "============================================\n\n";

$db_host = getenv('DB_HOST') ?: 'mysql';
$db_user = 'platform_admin';
$db_pass = getenv('DB_PASS') ?: '';   // never hardcode; injected at runtime
$db_name = 'acmetocasino_platform';

// Wait for MySQL to be fully ready
$max_retries = 30;
$mysqli = null;
for ($i = 0; $i < $max_retries; $i++) {
    $mysqli = @new mysqli($db_host, $db_user, $db_pass, $db_name);
    if (!$mysqli->connect_error) break;
    echo "Waiting for MySQL... ({$i}/{$max_retries})\n";
    sleep(2);
}

if ($mysqli->connect_error) {
    echo "FATAL: Could not connect to MySQL after {$max_retries} retries\n";
    exit(1);
}

echo "Connected to MySQL\n\n";

// ---------------------------------------------------------------------------
// PRE-TEST: Verify schema and test data
// ---------------------------------------------------------------------------
echo "--- PRE-TEST CHECKS ---\n";

$result = $mysqli->query("SELECT COUNT(*) as cnt FROM promotion_campaigns WHERE status = 'active' AND valid_until > NOW()");
$row = $result->fetch_assoc();
$active_campaigns = (int) $row['cnt'];
echo "Active campaigns: {$active_campaigns}\n";
assert($active_campaigns === 3, "Expected 3 active campaigns, got {$active_campaigns}");

$result = $mysqli->query("SELECT COUNT(*) as cnt FROM coupon_codes");
$row = $result->fetch_assoc();
$pre_codes = (int) $row['cnt'];
echo "Pre-existing codes: {$pre_codes}\n";
assert($pre_codes === 0, "Expected 0 codes before run, got {$pre_codes}");

echo "Pre-test checks PASSED\n\n";

// ---------------------------------------------------------------------------
// RUN: Execute coupon_generator.php with modified config
// ---------------------------------------------------------------------------
echo "--- RUNNING COUPON GENERATOR ---\n";

// Read the original script and patch for Docker environment
$script = file_get_contents('/app/coupon_generator.php');
$script = str_replace("\$db_host = 'localhost';", "\$db_host = '{$db_host}';", $script);
// Disable mail() to avoid errors in test container
$script = str_replace('if (mail(', 'if (false && mail(', $script);

$tmp_script = tempnam('/tmp', 'coupon_test_');
file_put_contents($tmp_script, $script);

// Run the script in a separate PHP process
$descriptors = [
    0 => ['pipe', 'r'],  // stdin
    1 => ['pipe', 'w'],  // stdout
    2 => ['pipe', 'w'],  // stderr
];

$process = proc_open(['php', $tmp_script], $descriptors, $pipes);
if (is_resource($process)) {
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[2]);
    $return_code = proc_close($process);
} else {
    echo "FATAL: Could not start PHP process\n";
    exit(1);
}

echo $stdout . "\n";
if ($stderr) echo "STDERR: {$stderr}\n";

unlink($tmp_script);

// ---------------------------------------------------------------------------
// POST-TEST: Validate results
// ---------------------------------------------------------------------------
echo "--- POST-TEST VALIDATION ---\n";

$tests_passed = 0;
$tests_failed = 0;

function test($name, $condition, &$passed, &$failed) {
    echo "Test: {$name}... ";
    if ($condition) {
        echo "PASS\n";
        $passed++;
    } else {
        echo "FAIL\n";
        $failed++;
    }
}

// Test 1: Script exited successfully
test("Script exit code is 0", $return_code === 0, $tests_passed, $tests_failed);

// Test 2: Codes were generated
$result = $mysqli->query("SELECT COUNT(*) as cnt FROM coupon_codes");
$row = $result->fetch_assoc();
$total_codes = (int) $row['cnt'];
test("Codes generated ({$total_codes} > 0)", $total_codes > 0, $tests_passed, $tests_failed);

// Test 3: Expected code count (50 + 25 + 100 = 175 from 3 campaigns)
test("Expected 175 codes total (got {$total_codes})", $total_codes === 175, $tests_passed, $tests_failed);

// Test 4: Codes are unique
$result = $mysqli->query("SELECT code, COUNT(*) as cnt FROM coupon_codes GROUP BY code HAVING cnt > 1");
$dupes = $result->num_rows;
test("No duplicate codes ({$dupes} dupes)", $dupes === 0, $tests_passed, $tests_failed);

// Test 5: Codes have correct prefixes
$result = $mysqli->query("
    SELECT c.code_prefix, COUNT(cc.code_id) as cnt,
           SUM(CASE WHEN cc.code LIKE CONCAT(c.code_prefix, '%') THEN 1 ELSE 0 END) as matching
    FROM promotion_campaigns c
    JOIN coupon_codes cc ON cc.campaign_id = c.campaign_id
    WHERE c.valid_until > NOW()
    GROUP BY c.campaign_id, c.code_prefix
");
$prefix_ok = true;
while ($row = $result->fetch_assoc()) {
    if ((int)$row['cnt'] !== (int)$row['matching']) {
        $prefix_ok = false;
        break;
    }
}
test("Code prefixes match campaigns", $prefix_ok, $tests_passed, $tests_failed);

// Test 6: Bonus amounts match
$result = $mysqli->query("
    SELECT COUNT(*) as cnt FROM coupon_codes cc
    JOIN promotion_campaigns c ON cc.campaign_id = c.campaign_id
    WHERE cc.bonus_amount != c.bonus_amount
");
$row = $result->fetch_assoc();
test("Bonus amounts match campaigns", (int)$row['cnt'] === 0, $tests_passed, $tests_failed);

// Test 7: No codes for expired campaign
$result = $mysqli->query("
    SELECT COUNT(*) as cnt FROM coupon_codes cc
    JOIN promotion_campaigns c ON cc.campaign_id = c.campaign_id
    WHERE c.campaign_name = 'Old Promo'
");
$row = $result->fetch_assoc();
test("No codes for expired campaign", (int)$row['cnt'] === 0, $tests_passed, $tests_failed);

// Test 8: All codes have expiry date
$result = $mysqli->query("SELECT COUNT(*) as cnt FROM coupon_codes WHERE valid_until IS NULL");
$row = $result->fetch_assoc();
test("All codes have expiry date", (int)$row['cnt'] === 0, $tests_passed, $tests_failed);

// Test 9: None redeemed
$result = $mysqli->query("SELECT COUNT(*) as cnt FROM coupon_codes WHERE redeemed = 1");
$row = $result->fetch_assoc();
test("No codes redeemed yet", (int)$row['cnt'] === 0, $tests_passed, $tests_failed);

// Test 10: Code length
$result = $mysqli->query("SELECT MIN(LENGTH(code)) as min_len, MAX(LENGTH(code)) as max_len FROM coupon_codes");
$row = $result->fetch_assoc();
test("Code length >= 8 chars (min={$row['min_len']})", (int)$row['min_len'] >= 8, $tests_passed, $tests_failed);

// Test 11: Per-campaign code count
$result = $mysqli->query("
    SELECT c.campaign_name, c.codes_per_batch as expected, COUNT(cc.code_id) as actual
    FROM promotion_campaigns c
    JOIN coupon_codes cc ON cc.campaign_id = c.campaign_id
    WHERE c.valid_until > NOW()
    GROUP BY c.campaign_id
");
while ($row = $result->fetch_assoc()) {
    test("Campaign '{$row['campaign_name']}': {$row['actual']}/{$row['expected']} codes",
         (int)$row['actual'] === (int)$row['expected'], $tests_passed, $tests_failed);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
echo "\n============================================\n";
echo "RESULTS: {$tests_passed} passed, {$tests_failed} failed\n";
echo "============================================\n";

$mysqli->close();

exit($tests_failed > 0 ? 1 : 0);
?>
