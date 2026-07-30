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

# shellcheck disable=SC1091,SC2046,SC2066,SC2086,SC2154,SC2164
#Bash script to update each site with the newest ng-shared changes and rebuild
source $(dirname $0)/brands-config.sh

if [[ $USER != 'build' ]];
then
    echo 'You must be logged in as build user'
    exit 0
fi

if [[ $# -eq 0 ]] ; then
    echo 'Please specify a group! 0 - 4'
    exit 0
fi

for elmv in "${!ngBrandGroups[$1]}"
do
	for folder in "${elmv}"
  	do
       	echo "$folder"
		cd /var/www/html/cms/$folder
		git reset --hard
		git pull
		npm update @acmetocasino/shared
		sh postpull.sh
  	done
done
