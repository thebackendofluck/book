#!/bin/bash
# API Gateway User Data Script
# Configures EC2 instance for YubiHSM API Gateway

set -e

# Variables from Terraform
API_PORT="${api_port}"
HSM_CONNECTOR_URL="${hsm_connector_url}"
HSM_AUTH_KEY_ID="${hsm_auth_key_id}"
HSM_PASSWORD_SSM="${hsm_password_ssm}"
CERT_DIR="${cert_dir}"
ALLOWED_IPS="${allowed_ips}"
RATE_LIMIT="${rate_limit}"
ENVIRONMENT="${environment}"

# Logging
exec > >(tee /var/log/user-data.log) 2>&1

echo "Starting API Gateway setup..."

# Update system
apt-get update
apt-get upgrade -y

# Install required packages
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    openssl \
    iptables \
    iptables-persistent \
    curl \
    jq \
    git \
    awscli \
    unzip

# Install YubiHSM SDK
wget -q https://developers.yubico.com/YubiHSM2/Releases/yubihsm2-sdk-latest.tar.gz
tar -xzf yubihsm2-sdk-latest.tar.gz
cd yubihsm2-sdk* && dpkg -i *.deb || apt-get install -f -y
cd ..

# Install Python packages
pip3 install fastapi uvicorn[standard] yubihsm[http,usb] cryptography

# Create application directory
mkdir -p /opt/api-gateway
cd /opt/api-gateway

# Copy API Gateway code
cp api_gateway.py api_gateway_firewall.sh generate_mtls_certs.sh /opt/api-gateway/

# Make scripts executable
chmod +x *.sh *.py

# Generate mTLS certificates
export CERT_DIR="$CERT_DIR"
export CLIENT_CNS="admin-client operator-client"
./generate_mtls_certs.sh

# Configure firewall
export API_PORT="$API_PORT"
export ALLOWED_IPS="$ALLOWED_IPS"
export RATE_LIMIT="$RATE_LIMIT"
./api_gateway_firewall.sh

# Get HSM password from SSM if configured.
# fail-fast: if HSM_PASSWORD_SSM is not provided and HSM_PASSWORD is not
# already in the environment, abort. We never fall back to a hardcoded
# default — see chapter notes on credential management.
if [ -n "$HSM_PASSWORD_SSM" ]; then
    HSM_PASSWORD=$(aws ssm get-parameter --name "$HSM_PASSWORD_SSM" --with-decryption --query Parameter.Value --output text)
fi
: "${HSM_PASSWORD:?HSM_PASSWORD must be set (provide via HSM_PASSWORD_SSM or env)}"

# Create environment file
cat > .env << EOF
API_PORT=$API_PORT
HSM_CONNECTOR_URL=$HSM_CONNECTOR_URL
HSM_AUTH_KEY_ID=$HSM_AUTH_KEY_ID
HSM_PASSWORD=$HSM_PASSWORD
SSL_CERT_FILE=$CERT_DIR/server.crt
SSL_KEY_FILE=$CERT_DIR/server.key
SSL_CA_CERTS=$CERT_DIR/ca-bundle.crt
ENVIRONMENT=$ENVIRONMENT
EOF

# Create systemd service
cat > /etc/systemd/system/api-gateway.service << EOF
[Unit]
Description=YubiHSM API Gateway
After=network.target
Wants=yubihsm-connector.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/api-gateway
EnvironmentFile=/opt/api-gateway/.env
ExecStart=/usr/local/bin/uvicorn api_gateway:app --host 0.0.0.0 --port $API_PORT --ssl-certfile $CERT_DIR/server.crt --ssl-keyfile $CERT_DIR/server.key --ssl-ca-certs $CERT_DIR/ca-bundle.crt --ssl-cert-reqs 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
systemctl daemon-reload
systemctl enable api-gateway
systemctl start api-gateway

# Configure CloudWatch logging
cat > /opt/awslogs-config-file << EOF
[general]
state_file = /var/awslogs/state/agent-state

[/var/log/api-gateway.log]
file = /var/log/api-gateway.log
log_group_name = /aws/ec2/yubihsm-api-gateway-$ENVIRONMENT
log_stream_name = {instance_id}
datetime_format = %Y-%m-%d %H:%M:%S
EOF

# Install CloudWatch agent
wget -q https://s3.amazonaws.com/aws-cloudwatch/downloads/latest/awslogs-agent-setup.py
python3 awslogs-agent-setup.py -n -r $(curl -s http://169.254.169.254/latest/meta-data/placement/region) -c /opt/awslogs-config-file

# Configure log rotation
cat > /etc/logrotate.d/api-gateway << EOF
/var/log/api-gateway.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    create 0644 root root
    postrotate
        systemctl reload api-gateway
    endscript
}
EOF

# Create health check script
cat > /usr/local/bin/health-check.sh << 'EOF'
#!/bin/bash
# Health check script for API Gateway

API_PORT=$1
CERT_DIR=$2

# Check if service is running
if ! systemctl is-active --quiet api-gateway; then
    echo "CRITICAL: API Gateway service not running"
    exit 2
fi

# Check if port is listening
if ! netstat -tln | grep -q ":$API_PORT "; then
    echo "CRITICAL: API Gateway not listening on port $API_PORT"
    exit 2
fi

# Check API health endpoint
if ! curl -k --cert "$CERT_DIR/admin-client.crt" --key "$CERT_DIR/admin-client.key" --cacert "$CERT_DIR/ca.crt" "https://localhost:$API_PORT/health" >/dev/null 2>&1; then
    echo "CRITICAL: API Gateway health check failed"
    exit 2
fi

echo "OK: API Gateway healthy"
exit 0
EOF

chmod +x /usr/local/bin/health-check.sh

# Set up cron job for health monitoring
echo "*/5 * * * * root /usr/local/bin/health-check.sh $API_PORT $CERT_DIR >> /var/log/health-check.log 2>&1" > /etc/cron.d/api-gateway-health

# Final setup
echo "API Gateway setup complete!"
echo "API Endpoint: https://$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4):$API_PORT"
echo "Health Check: /usr/local/bin/health-check.sh $API_PORT $CERT_DIR"

# Clean up
rm -f yubihsm2-sdk-latest.tar.gz
rm -rf yubihsm2-sdk*

echo "Setup finished successfully"