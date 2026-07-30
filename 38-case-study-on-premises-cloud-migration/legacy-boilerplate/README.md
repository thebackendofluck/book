# Legacy Boilerplate - PHP Casino Frontend

Production-derived legacy PHP frontend boilerplate from a white-label casino platform.
This represents the "before" state in a cloud migration journey.

## Context (Chapter 38 - Case Study: On-Premises to Cloud Migration)

This boilerplate was the starting point for every new white-label casino brand.
It demonstrates the legacy architecture that necessitated migration:

- **PHP 5.x** monolithic frontend with Apache mod_rewrite
- **MySQL CMS** backend (beast_cms) for content management
- **Oracle database** for the core gaming platform
- **Tight coupling** between brand config, CMS, and platform
- **No containerization** -- deployed directly to bare-metal servers
- **Grunt-based** asset pipeline (pre-webpack era)

## Structure

```
legacy-boilerplate/
  config.php          # Brand configuration (DB, CMS, site settings)
  index.php           # Entry point (delegates to shared CMS)
  .htaccess           # Apache rewrite rules (SEO URLs, HTTPS redirect)
  ajax/               # AJAX endpoints (balance, history, withdrawals)
  includes/           # PHP includes (headers, footers, shared logic)
  pages/              # Page templates
  css-source/         # CSS assets (including payment provider skins)
  js-source/          # JavaScript source files
  fonts/              # Web fonts
  images/             # Static images
  utils/              # Utility scripts
```

## Key Patterns (Anti-patterns by Modern Standards)

1. **Hardcoded credentials** in config.php (now uses Vault in production)
2. **Brand-specific Apache rules** instead of application-level routing
3. **Shared filesystem** (`/var/www/html/cms/shared`) across all brands
4. **No environment variables** -- config differs per deployment via file edits
5. **Direct database passwords** in source control

## Why This Matters

This boilerplate illustrates why the migration described in Chapter 38 was
necessary. Each new brand required copying this directory, editing config.php,
and deploying to shared infrastructure -- a process that took days and was
error-prone. The modern architecture replaced this with containerized
microservices, environment-injected configuration, and automated deployments.
