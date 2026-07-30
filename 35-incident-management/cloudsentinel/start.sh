#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.


# Replace the API key placeholder with the actual environment variable
find /usr/share/nginx/html -name "*.js" -exec sed -i "s/__APP_API_KEY_PLACEHOLDER__/${VITE_API_KEY}/g" {} \;

# Start nginx
nginx -g "daemon off;"