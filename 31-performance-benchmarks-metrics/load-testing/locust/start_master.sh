#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.



if [[ "${USE_PREREG}" == "true" ]]; then
	echo "Retrieving pregregister users..."
	python get_users.py
fi


locust --master --headless ${LOCUST_USER}

while true
do
	echo "Locust test completed."
	echo "Restart the master container to re-run the test."
	sleep 300
done