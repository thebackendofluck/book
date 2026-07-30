#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2029,SC2035,SC2086,SC2091,SC2164

USER=operator

ssh_get_arch_logs(){
    local SERVER=$1
    echo "Downloading from $SERVER"
    TMP_PATH="/tmp/platformlogs/$SERVER"
    mkdir -p $TMP_PATH

    TMP_LIST="$TMP_PATH/arch_logs.log"
    echo > $TMP_LIST
    ssh "$USER@$SERVER" 'sudo ls /tmp/tclogs/arch_logs' > "$TMP_LIST"

    while IFS= read -r file; do
        FILE=${TMP_PATH}/${file}

        if [ -f "$FILE" ]; then
            echo "$file already exists"
        else
            echo "Downloading $file"
            ssh $USER@$SERVER "sudo cat /tmp/tclogs/arch_logs/$file" > ${FILE} </dev/null
        fi
    done < $TMP_LIST

    pushd $TMP_PATH
    gunzip -fk *.gz
    popd

    echo "Downloading from current"
    ssh $USER@$SERVER 'sudo cat /tmp/tclogs/platform.log' > $TMP_PATH/platform.log </dev/null
    rm $TMP_LIST
    cp -r $TMP_PATH logs
}

ssh_get_arch_logs 'platform-mi.acmestage.com'
ssh_get_arch_logs 'platform-pa.acmestage.com'
ssh_get_arch_logs 'platform-hub.acmestage.com'
