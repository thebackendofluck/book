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

# shellcheck disable=SC2034,SC2086
#Bash script to grant access to a Team for multiple GH repositories
#Given a team and an array of repositories the script will call GH REST API to grant access to each repository.
#GH API Doc https://docs.github.com/en/rest/reference/teams#add-or-update-team-repository-permissions

#GH Username
username=
#GH Token
token=
#GH Organization
org=acmetocasino
#GH Team to modify
team=
#Permission to grant to the repos
permission=

teamid=$(curl -s admin:org -H "Authorization: Token $token" "https://api.github.com/orgs/$org/teams" | \
    jq --arg team "$team" '.[] | select(.name==$team) | .id')

brands=('brand-alpha' 'brand-bravo' 'brand-charlie' 'brand-delta' 'brand-echo' 'brand-foxtrot' 'brand-golf' 'brand-hotel' 'brand-india' 'brand-juliet' 'brand-kilo' 'brand-lima' 'brand-mike' 'brand-november' 'brand-oscar')

for b in "${brands[@]}"
do
  echo $b
  curl -s admin:org -H "Authorization: Token $token" -d '{"permission":"'"$permission"'"}' -X PUT "https://api.github.com/teams/$teamid/repos/$org/site-$b"
  curl -s admin:org -H "Authorization: Token $token" -d '{"permission":"'"$permission"'"}' -X PUT "https://api.github.com/teams/$teamid/repos/$org/ng-$b"
done
