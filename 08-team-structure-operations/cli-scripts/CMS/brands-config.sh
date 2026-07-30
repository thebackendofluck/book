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

# shellcheck disable=SC2034

prodWebHost='updater@web1.acmetocasino.com'

repos=('brand-alpha' 'brand-bravo' 'brand-charlie' 'brand-delta' 'brand-echo' 'brand-foxtrot' 'brand-golf' 'brand-hotel' 'brand-india' 'brand-juliet' 'brand-kilo' 'brand-lima' 'brand-mike' 'brand-november' 'brand-oscar' 'brand-papa' 'brand-quebec' 'brand-romeo' 'brand-sierra' 'brand-tango' 'brand-uniform' 'brand-victor' 'brand-whiskey' 'brand-xray' 'brand-yankee' 'brand-zulu')

brandDomains=('brand-alpha.com' 'brand-bravo.com' 'brand-charlie.com' 'brand-delta.com' 'brand-echo.com' 'brand-foxtrot.com' 'brand-golf.com' 'brand-hotel.com' 'brand-india.com' 'brand-juliet.com' 'brand-kilo.com' 'brand-lima.com' 'brand-mike.com' 'brand-november.com' 'brand-oscar.com' 'brand-papa.com' 'brand-quebec.com' 'brand-romeo.com' 'brand-sierra.com' 'brand-tango.com' 'brand-uniform.com' 'brand-victor.com' 'brand-whiskey.com' 'brand-xray.com' 'brand-yankee.com' 'brand-zulu.com')

brandersRepos=('brand-foxtrot' 'brand-golf' 'brand-hotel' 'brand-india' 'brand-juliet')
brandersDomains=('brand-foxtrot.com' 'brand-golf.com' 'brand-hotel.com' 'brand-india.com' 'brand-juliet.com')
brandersKeys=('aaaa1111bbbb2222cccc3333dddd4444' 'eeee5555ffff6666aaaa7777bbbb8888' 'cccc9999dddd0000eeee1111ffff2222' 'aaaa3333bbbb4444cccc5555dddd6666' 'eeee7777ffff8888aaaa9999bbbb0000')

ngBrandGroup0=('ng-brand-alpha' 'ng-brand-bravo' 'ng-brand-charlie' 'ng-brand-delta' 'ng-brand-echo')
ngBrandGroup1=('ng-brand-foxtrot' 'ng-brand-golf' 'ng-brand-hotel' 'ng-brand-india' 'ng-brand-juliet' 'interactions')
ngBrandGroup2=('ng-brand-kilo' 'ng-brand-lima' 'ng-brand-mike' 'ng-brand-november' 'ng-brand-oscar')
ngBrandGroup3=('ng-brand-papa' 'ng-brand-quebec' 'ng-brand-romeo' 'ng-brand-sierra' 'ng-brand-tango')
ngBrandGroup4=('ng-brand-uniform' 'ng-brand-victor' 'ng-brand-whiskey' 'ng-brand-xray' 'ng-brand-yankee')

ngBrandGroups=('ngBrandGroup0[@]' 'ngBrandGroup1[@]' 'ngBrandGroup2[@]' 'ngBrandGroup3[@]' 'ngBrandGroup4[@]')
