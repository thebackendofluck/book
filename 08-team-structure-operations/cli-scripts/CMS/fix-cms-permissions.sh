#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC1091,SC2046,SC2086,SC2154
source $(dirname $0)/brands-config.sh

if [[ $USER != 'root' ]];
then
    echo 'You must be logged in as root'
    exit 0
fi

chown -R build /var/www/html/cms

for brand in "${brandersRepos[@]}"
do
  chown -R apache /var/www/html/cms/$brand/_storage/
  chown -R apache /var/www/html/cms/$brand/_laravel/bootstrap/cache/
done
