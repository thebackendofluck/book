<?php
// ============================================================================
// Casino Backoffice -- Architecture Overview (reference implementation)
// ============================================================================
// This file illustrates a clean, layered structure for a casino backoffice:
// HTTP handling, business logic, and data access are separated, credentials
// come from the environment, and every query is parameterised. It is the
// target architecture the rest of the chapter builds toward, replacing the
// monolithic "one PHP file per screen with SQL inlined" panels common in
// platforms built between 2005 and 2015.
// ============================================================================

declare(strict_types=1);

// -- Bootstrap: configuration from the environment, not from source ----------
// Nothing sensitive lives in code. A secrets manager (Vault, AWS Secrets
// Manager) injects the values; the app fails fast if a required one is absent.
function env_required(string $key): string {
    $v = getenv($key);
    if ($v === false || $v === '') {
        throw new RuntimeException("Missing required environment variable: {$key}");
    }
    return $v;
}

define('PAGE_LIMIT', 15);
define('CUSTOMER_DETAILS_PAGE_LIMIT', 28);
define('DEFAULT_TIME_ZONE', 'Europe/Dublin');
define('BO_TITLE', 'Platform');

// ============================================================================
// DIRECTORY STRUCTURE (layered, not one-file-per-screen)
// ============================================================================
// backoffice/
// ├── public/
// │   └── index.php               # Single front controller; routes requests
// ├── config/
// │   └── config.php              # Reads env vars; no secrets in the file
// ├── src/
// │   ├── Http/                   # Controllers: parse request, return response
// │   │   ├── CustomerController.php
// │   │   ├── ComplianceController.php
// │   │   └── MarketingController.php
// │   ├── Service/                # Business logic, framework-agnostic
// │   │   ├── CustomerService.php
// │   │   ├── BonusService.php
// │   │   └── ReportingService.php
// │   ├── Repository/             # Data access; parameterised SQL only
// │   │   ├── CustomerRepository.php
// │   │   └── BrandConfigRepository.php
// │   ├── Security/               # AuthN/AuthZ (OIDC/Keycloak), RBAC, audit
// │   │   ├── Authenticator.php
// │   │   └── ActivityLog.php
// │   └── Support/                # Shared utilities
// ├── templates/                  # View layer (Twig), no logic
// └── jobs/                       # Scheduled workers (systemd timer / CronJob)
//     ├── CouponGenerator.php     # Reuses the same Service + Repository layer
//     └── LoyaltyRecalculator.php
//
// KEY PRINCIPLES:
// 1. One front controller; routing is explicit, not "URL maps to a PHP file".
// 2. SQL lives only in repositories, always parameterised (no string building).
// 3. HTTP, business logic, and data access are separate and independently
//    testable layers.
// 4. Multi-brand support is a first-class parameter threaded through the
//    services, not a pile of global config constants.
// 5. Jobs reuse the Service/Repository layers instead of re-bootstrapping the
//    whole app with their own DB connection.
// ============================================================================

// -- Data access: a repository with parameterised queries --------------------

final class BrandConfigRepository {
    public function __construct(private mysqli $db) {}

    /** @return array<string,string> */
    public function getBrandSettings(int $brandId): array {
        // Prepared statement: the brand id is BOUND, never concatenated, so
        // there is no SQL-injection surface even if the caller is careless.
        $stmt = $this->db->prepare(
            'SELECT config_key, config_value
               FROM brand_config
              WHERE brand_id = ?
           ORDER BY config_key'
        );
        $stmt->bind_param('i', $brandId);
        $stmt->execute();
        $result = $stmt->get_result();

        $settings = [];
        while ($row = $result->fetch_assoc()) {
            $settings[$row['config_key']] = $row['config_value'];
        }
        $stmt->close();
        return $settings;
    }
}

// -- Business logic: a service that depends on the repository, not on SQL -----

final class BrandService {
    public function __construct(private BrandConfigRepository $brands) {}

    /** @return array<string,string> */
    public function settingsFor(int $brandId): array {
        // Pure business logic here; no SQL, no HTTP. Trivial to unit-test with
        // a fake repository.
        return $this->brands->getBrandSettings($brandId);
    }
}

// -- HTTP layer: a thin controller that only translates request to response --

final class BrandController {
    public function __construct(private BrandService $brands) {}

    public function show(int $brandId): string {
        $settings = $this->brands->settingsFor($brandId);
        // A real controller renders a Twig template; kept inline here for the
        // overview. The point is the controller holds no SQL and no rules.
        return json_encode(['brand_id' => $brandId, 'settings' => $settings]);
    }
}
