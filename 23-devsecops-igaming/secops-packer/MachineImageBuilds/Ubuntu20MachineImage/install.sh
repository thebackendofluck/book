#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC1091

# CIS Benchmark hardening script for Ubuntu 20.04 AMIs
# Executes 40+ security hardening functions at image build time
# Part of the immutable infrastructure pipeline for regulated iGaming

# shellcheck disable=1090
# shellcheck disable=2009
# shellcheck disable=2034

set -u -o pipefail
SCRIPT_COUNT="0"

if ! ps -p $$ | grep -si bash; then
  echo "Sorry, this script requires bash."
  exit 1
fi

if ! [ -x "$(command -v systemctl)" ]; then
  echo "systemctl required. Exiting."
  exit 1
fi

function main {
  ((SCRIPT_COUNT++)) && echo "[$SCRIPT_COUNT] Initialize hardening"
  DEBIAN_FRONTEND=noninteractive apt-get -y clean
  DEBIAN_FRONTEND=noninteractive apt-get -y update
  DEBIAN_FRONTEND=noninteractive apt-get install -y arp-scan net-tools snapd
  REQUIREDPROGS='arp w'
  REQFAILED=0
  for p in $REQUIREDPROGS; do
    if ! command -v "$p" >/dev/null 2>&1; then
      echo "$p is required."
      REQFAILED=1
    fi
  done

  ## AWS Systems Manager Agent
  if ! [[ $(systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service) ]]; then
    snap install amazon-ssm-agent --classic
    systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
    systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service
  else
    systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
    systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service
  fi
  systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service

  if [ $REQFAILED = 1 ]; then
    echo 'net-tools and procps packages has to be installed.'
    exit 1
  fi

  ## Datadog Agent
  readonly rootdir="/packer/build/"
  curl -L https://s3.amazonaws.com/dd-agent/scripts/install_script.sh > "${rootdir}/dd_install.sh"
  chmod 777 "${rootdir}/dd_install.sh"
  bash "${rootdir}/dd_install.sh"
  if [[ $(systemctl status datadog-agent) ]]; then
    systemctl stop datadog-agent
  fi

  ARPBIN="$(command -v arp)"
  WBIN="$(command -v w)"
  LXC="0"
  SERVERIP="$(ip route | grep '^default' | awk '{print $9}')"

  if grep -qE 'container=lxc|container=lxd' /proc/1/environ; then
    LXC="1"
  fi

  if grep -s "AUTOFILL='Y'" /packer/build/ubuntu.cfg; then
    USERIP="$($WBIN -ih | awk '{print $3}' | head -n1)"

    if [[ "$USERIP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      ADMINIP="$USERIP"
    else
      ADMINIP="$(hostname -I | sed -E 's/\.[0-9]+ /.0\/24 /g')"
    fi

    sed -i "s/FW_ADMIN='/FW_ADMIN='$ADMINIP /" /packer/build/ubuntu.cfg
    sed -i "s/SSH_GRPS='/SSH_GRPS='$(id "$($WBIN -ih | awk '{print $1}' | head -n1)" -ng) /" /packer/build/ubuntu.cfg
    sed -i "s/CHANGEME=''/CHANGEME='$(date +%s)'/" /packer/build/ubuntu.cfg
    sed -i "s/VERBOSE='N'/VERBOSE='Y'/" /packer/build/ubuntu.cfg
  fi

  source /packer/build/ubuntu.cfg

  for s in /packer/build/scripts/*; do
    [[ -f $s ]] || break
    source "$s"
  done

  # Execute CIS hardening functions in order
  f_pre
  f_kernel
  f_disablenet
  f_disablefs
  f_disablemod
  f_systemdconf
  f_resolvedconf
  f_logindconf
  f_journalctl
  f_timesyncd
  f_fstab
  f_prelink
  f_aptget_configure
  f_aptget
  f_hosts
  f_issue
  f_sudo
  f_logindefs
  f_sysctl
  f_limitsconf
  f_adduser
  f_rootaccess
  f_package_install
  f_psad
  f_coredump
  f_usbguard
  f_postfix
  f_apport
  f_motdnews
  f_rkhunter
  f_sshconfig
  f_sshdconfig
  f_password
  f_cron
  f_ctrlaltdel
  f_auditd
  f_aide
  f_rhosts
  f_users
  f_lockroot
  f_package_remove
  f_suid
  f_restrictcompilers
  f_umask
  f_path
  f_aa_enforce
  f_aide_post
  f_aide_timer
  f_aptget_noexec
  f_aptget_clean
  f_systemddelta
  f_post
  f_checkreboot

  echo
}

LOGFILE="hardening-$(hostname --short)-$(date +%y%m%d).log"
echo "[HARDENING LOG - $(hostname --fqdn) - $(LANG=C date)]" >> "$LOGFILE"

main "$@" | tee -a "$LOGFILE"
