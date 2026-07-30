# CLI Scripts - Casino Operations Tooling

Administrative CLI tools used by the operations team to manage multi-brand casino CMS deployments. These scripts ran on the main staging server and handled day-to-day operations like cache clearing, code deployments, and permission management.

## CMS Scripts

Run from `/home/build/scripts` on the main stage server.

Script | Arguments | User | Description
--- | --- | --- | ---
brands-config.sh | N/A | N/A | Config file where brands and repositories are defined
clear-games-cache.sh | 1. `slow` | root | Clears Redis games cache on prod. Use `slow` during peak hours.
fix-cms-permissions.sh | N/A | root | Resets file permissions for CMS websites if changed incorrectly.
ng-shared-check-version.sh | N/A | build | Checks which version of ng-shared is being used per brand.
ng-shared-update.sh | 1. group `0-4` | build | Updates a group of sites with newest ng-shared changes and rebuilds.
run-ng-shared-update.sh | N/A | build | Updates all sites with latest ng-shared (runs 5 copies in parallel).
pull-all-sites.sh | 1. group `0-4` | build | Git hard reset + pull for site and ng repos, then rebuild per group.
run-pull-all-sites.sh | N/A | build | Runs pull-all-sites.sh for all brands (5 copies in parallel).
release-scontent-static.sh | 1. `js`/`css`/`images` 2. `dry`* | build | Pushes static assets from stage to S3 bucket on prod.
sync_brand.sh | 1. `brand/folder` 2. `dry`* | root | Releases sites or shared components to production.
sync_ng-brand.sh | 1. `brand/folder` 2. `dry`* | root | Pushes only ng (Angular) files for a release.
sync_site-brand.sh | 1. `brand/folder` 2. `dry`* | root | Pushes only website files for a release.

*Optional 2nd parameter `dry` runs a dry-run to preview changes.

## GitHub Scripts

Script | Description
--- | ---
add-repos-to-teams.sh | Grants GitHub team access to multiple brand repositories via REST API.

## Key Patterns for Book Readers

1. **Brand grouping for parallel operations**: 30+ brands split into 5 groups for parallel rebuilds
2. **Batched cache clearing**: Clears 5 brands at a time with configurable delay between batches
3. **S3 static asset pipeline**: Stage-to-S3-to-CloudFront deployment for JS/CSS/images
4. **PHP-FPM per-brand isolation**: Each brand gets its own FPM pool for process isolation
5. **GitHub API automation**: Bulk team permission management across dozens of repositories

## Chapter Reference

Chapter 8: Team Structure and Operations
