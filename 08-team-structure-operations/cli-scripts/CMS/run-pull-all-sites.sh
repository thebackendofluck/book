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

# shellcheck disable=SC2016
if [[ $USER != 'build' ]];
then
    echo 'You must be logged in as `build`'
    exit 0
fi

./pull-all-sites.sh 0 > logs/update-0.log 2>&1 &
./pull-all-sites.sh 1 > logs/update-1.log 2>&1 &
./pull-all-sites.sh 2 > logs/update-2.log 2>&1 &
./pull-all-sites.sh 3 > logs/update-3.log 2>&1 &
./pull-all-sites.sh 4 > logs/update-4.log 2>&1 &
