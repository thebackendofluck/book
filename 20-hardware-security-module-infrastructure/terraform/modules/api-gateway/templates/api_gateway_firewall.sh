#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# API Gateway Firewall Setup
# Configures iptables rules for secure API Gateway operation with mTLS

set -e

# Configuration
API_PORT=${API_PORT:-8443}
SSH_PORT=${SSH_PORT:-22}
ALLOWED_IPS=${ALLOWED_IPS:-""}  # Space-separated list of allowed IPs
RATE_LIMIT=${RATE_LIMIT:-100}   # Requests per minute per IP

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

backup_current_rules() {
    log_info "Backing up current iptables rules..."
    iptables-save > "/root/iptables_backup_$(date +%Y%m%d_%H%M%S).rules"
    ip6tables-save > "/root/ip6tables_backup_$(date +%Y%m%d_%H%M%S).rules" 2>/dev/null || true
}

flush_rules() {
    log_info "Flushing existing rules..."
    iptables -F
    iptables -X
    iptables -t nat -F
    iptables -t nat -X
    iptables -t mangle -F
    iptables -t mangle -X
}

set_default_policies() {
    log_info "Setting default policies..."
    # Default deny all
    iptables -P INPUT DROP
    iptables -P FORWARD DROP
    iptables -P OUTPUT ACCEPT
}

allow_loopback() {
    log_info "Allowing loopback interface..."
    iptables -A INPUT -i lo -j ACCEPT
    iptables -A OUTPUT -o lo -j ACCEPT
}

allow_established_connections() {
    log_info "Allowing established and related connections..."
    iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
}

allow_ssh() {
    log_info "Allowing SSH access..."
    if [ -n "$ALLOWED_IPS" ]; then
        for ip in $ALLOWED_IPS; do
            iptables -A INPUT -p tcp -s "$ip" --dport "$SSH_PORT" -m conntrack --ctstate NEW -j ACCEPT
            log_info "Allowed SSH from $ip"
        done
    else
        # Allow SSH from anywhere (not recommended for production)
        log_warn "No ALLOWED_IPS specified - allowing SSH from anywhere"
        iptables -A INPUT -p tcp --dport "$SSH_PORT" -m conntrack --ctstate NEW -j ACCEPT
    fi
}

setup_api_gateway_rules() {
    log_info "Setting up API Gateway firewall rules..."
    
    # Create custom chains for rate limiting and logging
    iptables -N API_GATEWAY
    iptables -N API_RATE_LIMIT
    iptables -N API_LOG_DROP
    
    # Rate limiting chain
    iptables -A API_RATE_LIMIT -m limit --limit "${RATE_LIMIT}/minute" --limit-burst "$((RATE_LIMIT * 2))" -j RETURN
    iptables -A API_RATE_LIMIT -j API_LOG_DROP
    
    # Logging and drop chain
    iptables -A API_LOG_DROP -m limit --limit 5/minute -j LOG --log-prefix "API_GATEWAY_DROP: " --log-level 4
    iptables -A API_LOG_DROP -j DROP
    
    # API Gateway chain
    iptables -A API_GATEWAY -p tcp --dport "$API_PORT" -m conntrack --ctstate NEW -j API_RATE_LIMIT
    iptables -A API_GATEWAY -p tcp --dport "$API_PORT" -j ACCEPT
    
    # Apply API Gateway rules
    if [ -n "$ALLOWED_IPS" ]; then
        for ip in $ALLOWED_IPS; do
            iptables -A INPUT -p tcp -s "$ip" --dport "$API_PORT" -j API_GATEWAY
            log_info "Allowed API access from $ip"
        done
    else
        # Allow API access from anywhere (requires mTLS authentication)
        log_warn "No ALLOWED_IPS specified - allowing API access from anywhere (mTLS required)"
        iptables -A INPUT -p tcp --dport "$API_PORT" -j API_GATEWAY
    fi
}

allow_icmp() {
    log_info "Allowing ICMP (ping)..."
    iptables -A INPUT -p icmp --icmp-type echo-request -m limit --limit 1/second -j ACCEPT
}

allow_dns() {
    log_info "Allowing DNS queries..."
    iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
    iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
}

allow_http_https_outbound() {
    log_info "Allowing outbound HTTP/HTTPS..."
    iptables -A OUTPUT -p tcp --dport 80 -j ACCEPT
    iptables -A OUTPUT -p tcp --dport 443 -j ACCEPT
}

allow_ntp() {
    log_info "Allowing NTP..."
    iptables -A OUTPUT -p udp --dport 123 -j ACCEPT
}

setup_fail2ban_integration() {
    log_info "Setting up Fail2Ban integration..."
    # Create chain for Fail2Ban
    iptables -N f2b-sshd
    iptables -A INPUT -p tcp --dport "$SSH_PORT" -j f2b-sshd
    iptables -A f2b-sshd -j RETURN
}

log_final_rules() {
    log_info "Final iptables rules:"
    iptables -L -n -v
    echo ""
    log_info "Custom chains:"
    iptables -L API_GATEWAY -n -v
    iptables -L API_RATE_LIMIT -n -v
    iptables -L API_LOG_DROP -n -v
}

save_rules() {
    log_info "Saving iptables rules..."
    iptables-save > /etc/iptables/rules.v4
    ip6tables-save > /etc/iptables/rules.v6 2>/dev/null || true
}

create_monitoring_script() {
    log_info "Creating firewall monitoring script..."
    
    cat > /usr/local/bin/monitor_firewall.sh << 'EOF'
#!/bin/bash
# Firewall monitoring script

echo "=== Firewall Status ==="
echo "Date: $(date)"
echo ""

echo "Active connections to API Gateway:"
netstat -tn | grep ":8443 " | wc -l
echo ""

echo "Recent blocked connections:"
dmesg | grep "API_GATEWAY_DROP" | tail -10
echo ""

echo "Rate limiting status:"
iptables -L API_RATE_LIMIT -n -v
echo ""

echo "Top source IPs:"
iptables -L INPUT -n -v | grep -E "(ACCEPT|DROP)" | awk '{print $8}' | sort | uniq -c | sort -nr | head -10
EOF
    
    chmod +x /usr/local/bin/monitor_firewall.sh
    log_info "Monitoring script created at /usr/local/bin/monitor_firewall.sh"
}

main() {
    echo "=============================================="
    echo "  API Gateway Firewall Setup"
    echo "=============================================="
    echo ""
    
    check_root
    backup_current_rules
    flush_rules
    set_default_policies
    allow_loopback
    allow_established_connections
    allow_ssh
    setup_api_gateway_rules
    allow_icmp
    allow_dns
    allow_http_https_outbound
    allow_ntp
    setup_fail2ban_integration
    
    echo ""
    log_final_rules
    save_rules
    create_monitoring_script
    
    echo ""
    echo "=============================================="
    log_info "Firewall setup complete!"
    echo "=============================================="
    echo ""
    echo "Configuration:"
    echo "  API Port: $API_PORT"
    echo "  SSH Port: $SSH_PORT"
    echo "  Rate Limit: $RATE_LIMIT requests/minute"
    if [ -n "$ALLOWED_IPS" ]; then
        echo "  Allowed IPs: $ALLOWED_IPS"
    else
        echo "  Allowed IPs: ANY (mTLS authentication required)"
    fi
    echo ""
    echo "Monitoring:"
    echo "  Run: /usr/local/bin/monitor_firewall.sh"
    echo ""
    echo "Next steps:"
    echo "1. Test SSH access from allowed IPs"
    echo "2. Test API access with valid client certificate"
    echo "3. Monitor logs: tail -f /var/log/syslog | grep API_GATEWAY"
    echo "4. Consider installing Fail2Ban for additional SSH protection"
    echo ""
}

# Run main function
main "$@"