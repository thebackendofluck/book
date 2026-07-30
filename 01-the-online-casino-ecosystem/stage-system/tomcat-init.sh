#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2009,SC2086
# chkconfig: 3 60 30
# description: Starts and stops Tomcat (running as root)
# AcmetoCasino legacy platform - Tomcat init script
# The platform Java application runs inside this Tomcat instance

mode=$1
ps -ef | grep '/usr/local/tomcat' | grep -v grep | awk '{print $2}' > /usr/local/tomcat/tomcat.pid
PID=$(cat /usr/local/tomcat/tomcat.pid)

export TOMCAT_HOME=/usr/local/tomcat
export CATALINA_HOME=/usr/local/tomcat
export JAVA_HOME=/usr/local/java

case "$mode" in
  'start')
    echo "Starting Tomcat"
    su -c "$TOMCAT_HOME/bin/catalina.sh $mode" root
    ;;

  'stop')
    echo "Stopping Tomcat"
    $TOMCAT_HOME/bin/catalina.sh $mode
    ;;

  'rs')
    # Restart: stop, wait, force-kill, start, tail logs
    echo "Stopping Tomcat"
    $TOMCAT_HOME/bin/catalina.sh stop
    sleep 10
    kill -9 ${PID}
    echo "Starting Tomcat"
    $TOMCAT_HOME/bin/catalina.sh start
    tail -f $TOMCAT_HOME/logs/catalina.out
    ;;

  *)
    echo "usage: $0 start|stop|rs"
    exit 1
    ;;
esac
