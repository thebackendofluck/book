# Containerized run of the chapter-23 change-management Laravel 12 e2e harness.
# Build context must be the change-management/ root: the harness symlinks its
# app/Models and migrations into ../app and ../database, so both trees have to
# be present in the image for the links to resolve.
FROM php:8.5-cli

RUN apt-get update \
    && apt-get install -y --no-install-recommends git unzip sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /srv/change-management
COPY app/ app/
COPY database/ database/
COPY harness/ harness/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /srv/change-management/harness
# APP_DEBUG stays off: the rig is reachable on the LAN and a debug error page
# leaks stack traces, file paths, and SQL. APP_KEY is generated at container
# start (see entrypoint) so no secret is baked into image layers.
RUN composer install --no-interaction --prefer-dist --no-progress \
    && cp .env.example .env \
    && sed -i 's/^APP_DEBUG=.*/APP_DEBUG=false/' .env \
    && chmod +x /usr/local/bin/entrypoint.sh

# CIS 4.1 - run as non-root. The entrypoint writes to .env (key:generate),
# database/database.sqlite, and storage/ at container start, so the whole
# tree is handed to the runtime user rather than staying root-owned.
RUN groupadd -r appuser && useradd -r -g appuser -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /srv/change-management
USER appuser

EXPOSE 8000

# Laravel's default health route (bootstrap/app.php -> health: '/up').
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD php -r "exit(@file_get_contents('http://127.0.0.1:8000/up') === false ? 1 : 0);"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
