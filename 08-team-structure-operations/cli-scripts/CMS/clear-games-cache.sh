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
#Bash script to clear games cache for websites in batches to minimize impact on rebuilding cache when cache cleared.
source $(dirname $0)/brands-config.sh

delay=30

if [[ $1 == 'slow' ]] ; then
    echo 'Script will be run in slow mode...'
    delay=180
fi

echo "Running clear cache for sites...."

for domain in "${brandDomains[@]}"
do
    response=$(curl -X POST -s -d "req=clearcache&type=games" "https://www.$domain/ajax/ajaxhandler.php")

    echo "$domain: $response"

    i=$((i+1))

    if [ "$i" -eq "5" ]; then
       echo "Waiting $delay seconds before doing next batch...."
       sleep $delay
       i=0
    fi
done

i=0

for domain in "${brandersDomains[@]}"
do
  response=$(curl -X POST -s -H "Apikey: ${brandersKeys[i]}" "https://www.$domain/framework/clear")
  echo "$domain: $response"
  i=$((i+1))
done
