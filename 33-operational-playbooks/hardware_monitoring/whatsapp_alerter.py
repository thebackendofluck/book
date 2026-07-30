#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
WhatsApp Alerting Integration for Hardware Monitoring - Chapter 23: Operational Playbooks

Flask webhook service that receives alerts from Prometheus AlertManager and
forwards them via WhatsApp using the Twilio API. Supports multi-recipient delivery,
severity-based message formatting, and a /health endpoint for container healthchecks.

Environment variables required:
    TWILIO_ACCOUNT_SID    - Twilio account SID
    TWILIO_AUTH_TOKEN     - Twilio auth token
    TWILIO_WHATSAPP_FROM  - Twilio sandbox number (default: whatsapp:+14155238886)
    ALERT_WHATSAPP_NUMBERS - Comma-separated recipient numbers (e.g., +1234567890,+0987654321)
    PORT                  - HTTP port (default: 8080)

Usage:
    python whatsapp_alerter.py

Part of the iGaming Platform Engineering book.
"""

import os
import logging
from flask import Flask, request, jsonify
from twilio.rest import Client  # ty:ignore[unresolved-import]
from twilio.twiml.messaging_response import MessagingResponse  # ty:ignore[unresolved-import]
import json
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class WhatsAppAlerter:
    def __init__(self):
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.from_whatsapp = os.getenv('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
        self.to_numbers = os.getenv('ALERT_WHATSAPP_NUMBERS', '').split(',')

        if not all([self.account_sid, self.auth_token]):
            raise ValueError("Twilio credentials not configured")

        self.client = Client(self.account_sid, self.auth_token)
        logger.info("WhatsApp alerter initialized")

    def send_alert(self, alert_data: dict):
        """Send hardware alert via WhatsApp"""
        try:
            severity = alert_data.get('severity', 'info')
            component_type = alert_data.get('component_type', 'unknown')
            component_id = alert_data.get('component_id', 'unknown')
            risk_score = alert_data.get('risk_score', 0)
            description = alert_data.get('description', 'Hardware monitoring alert')

            # Create alert message
            emoji_map = {
                'critical': '🚨',
                'warning': '⚠️',
                'info': 'ℹ️'
            }

            emoji = emoji_map.get(severity.lower(), '📢')

            message = f"""{emoji} *HARDWARE ALERT*

*Severity:* {severity.upper()}
*Component:* {component_type}/{component_id}
*Risk Score:* {risk_score}
*Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{description}

_Please check Grafana dashboard for details_"""

            # Send to all configured numbers
            sent_count = 0
            for number in self.to_numbers:
                if number.strip():
                    try:
                        self.client.messages.create(
                            body=message,
                            from_=self.from_whatsapp,
                            to=f'whatsapp:{number.strip()}'
                        )
                        sent_count += 1
                        logger.info(f"WhatsApp alert sent to {number}")
                    except Exception as e:
                        logger.error(f"Failed to send WhatsApp to {number}: {e}")

            return {"status": "success", "messages_sent": sent_count}

        except Exception as e:
            logger.error(f"Failed to send WhatsApp alert: {e}")
            return {"status": "error", "error": str(e)}


alerter = WhatsAppAlerter()


@app.route('/alert', methods=['POST'])
def receive_alert():
    """Receive alerts from AlertManager and forward via WhatsApp"""
    try:
        alert_data = request.get_json()

        if not alert_data:
            return jsonify({"error": "No alert data received"}), 400

        # Extract alert information
        alerts = alert_data.get('alerts', [])
        for alert in alerts:
            labels = alert.get('labels', {})
            annotations = alert.get('annotations', {})

            whatsapp_alert = {
                'severity': labels.get('severity', 'info'),
                'component_type': labels.get('component_type', 'unknown'),
                'component_id': labels.get('component_id', 'unknown'),
                'risk_score': labels.get('risk_score', 0),
                'description': annotations.get('description', 'Hardware monitoring alert')
            }

            result = alerter.send_alert(whatsapp_alert)
            logger.info(f"WhatsApp alert result: {result}")

        return jsonify({"status": "alerts_processed", "count": len(alerts)}), 200

    except Exception as e:
        logger.error(f"Error processing alert: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
