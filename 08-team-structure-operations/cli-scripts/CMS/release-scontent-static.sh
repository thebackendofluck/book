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

# shellcheck disable=SC2046,SC2086
if [[ $USER != 'build' ]];
then
    echo 'You must be logged in as build user'
    exit 0
fi

if [ -z "$1" ]
  then
    echo 'Please specify "css", "js" or "images" as an argument'
    exit 0
fi

if [[ $2 == 'dry' ]] ; then
    echo 'Running sync with dry-run option....'
    aws s3 sync --dryrun /var/www/html/scontent/$1/  s3://scontent-acme/$1/
    exit 0
fi

if [[ $1 != 'images' ]] ; then
    mkdir /home/build/backup/scontent-$1-$(date +%F)
    echo "**** Backing up files **** "
    aws s3 sync s3://scontent-acme/$1/ /home/build/backup/scontent-$1-$(date +%F)
fi

echo "**** Syncing to S3 ****"
aws s3 sync /var/www/html/scontent/$1/  s3://scontent-acme/$1/


if [[ $1 != 'images' ]] ; then
    echo "**** Invalidating cache on cloudfront ****"
    aws cloudfront create-invalidation --distribution-id EXXXXXXXXXXXXX --paths "/$1/*"
fi
