#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.


echo "Reached pre-stop script"

timestamp="$(date +%Y%m%d)_$(date +%H%M%S)"

# Rename files with a timestamp
cp locust_stats.csv "locust_stats_$timestamp.csv"
cp locust_failures.csv "locust_failures_$timestamp.csv"
cp locust_stats_history.csv "locust_stats_history_$timestamp.csv"
cp locust_failed_requests.csv "locust_failed_requests_$timestamp.csv"

# Copy to s3
aws s3 cp "locust_stats_$timestamp.csv" s3://pengineering-jenkins-misc/locust_stats/
aws s3 cp "locust_failures_$timestamp.csv" s3://pengineering-jenkins-misc/locust_failures/
aws s3 cp "locust_stats_history_$timestamp.csv" s3://pengineering-jenkins-misc/locust_stats_history/
aws s3 cp "locust_failed_requests_$timestamp.csv" s3://pengineering-jenkins-misc/locust_failed_requests/
