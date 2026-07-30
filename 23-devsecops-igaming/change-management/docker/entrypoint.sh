#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -e

if grep -q '^APP_KEY=$' .env; then
    php artisan key:generate --force
fi

touch database/database.sqlite
php artisan migrate --force

exec php artisan serve --host=0.0.0.0 --port=8000
