#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# SED SSD Integration Flowchart Generator
# Creates visual flowcharts for SED SSD setup and management processes

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, ConnectionPatch
import numpy as np
import textwrap

def create_sed_workflow_flowchart():
    """Create comprehensive SED SSD workflow flowchart"""

    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12)
    ax.axis('off')

    # Title
    ax.set_title('SED SSD with YubiHSM 2 - Complete Workflow', fontsize=18, fontweight='bold', pad=20)

    # Define colors
    colors = {
        'start': '#e8f5e9',      # Light green
        'process': '#e3f2fd',    # Light blue
        'decision': '#fff3e0',   # Light orange
        'io': '#f3e5f5',         # Light purple
        'hsm': '#f9f9f9',        # Light gray
        'security': '#ffebee',  # Light red
        'end': '#e8f5e9'         # Light green
    }

    def add_node(x, y, text, color, width=2.5, height=0.8):
        """Add a process node"""
        rect = FancyBboxPatch((x-width/2, y-height/2), width, height,
                            boxstyle="round,pad=0.1", facecolor=color,
                            edgecolor='black', linewidth=1.5)
        ax.add_patch(rect)

        # Wrap text
        wrapped_text = textwrap.fill(text, width=20)
        ax.text(x, y, wrapped_text, ha='center', va='center',
               fontsize=9, fontweight='bold')

        return rect

    def add_decision(x, y, text, color):
        """Add a decision diamond"""
        diamond = mpatches.Polygon([[x, y+0.4], [x+0.8, y], [x, y-0.4], [x-0.8, y]],
                                 facecolor=color, edgecolor='black', linewidth=1.5)
        ax.add_patch(diamond)

        wrapped_text = textwrap.fill(text, width=15)
        ax.text(x, y, wrapped_text, ha='center', va='center',
               fontsize=8, fontweight='bold')

        return diamond

    def add_arrow(start_x, start_y, end_x, end_y, label="", style="-|>"):
        """Add connection arrow"""
        con = ConnectionPatch((start_x, start_y), (end_x, end_y), "data", "data",
                            arrowstyle=style, shrinkA=5, shrinkB=5,
                            mutation_scale=15, fc="black", linewidth=1.5)
        ax.add_patch(con)

        if label:
            # Position label at midpoint
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom',
                   fontsize=8, bbox=dict(boxstyle="round,pad=0.2", facecolor="white"))

    # Start node
    start = add_node(8, 11, 'SED SSD\nDeployment\nRequired', colors['start'])

    # Initial checks
    check1 = add_node(8, 9.5, 'Check Prerequisites\n(YubiHSM 2, sedutil-cli,\nSED-capable SSD)', colors['process'])

    # Device detection
    detect = add_node(8, 8, 'Detect SED Devices\n(sedutil-cli --scan,\nhdparm -I)', colors['process'])

    # Device selection decision
    select_decision = add_decision(8, 6.5, 'SED Device\nFound?', colors['decision'])

    # No device found
    no_device = add_node(5, 5.5, 'ERROR: No SED\nDevices Detected\nCheck Hardware', colors['security'])

    # Device found - get info
    device_info = add_node(11, 5.5, 'Get Device Info\n(Serial, Model,\nTCG Standard)', colors['process'])

    # Initialize decision
    init_decision = add_decision(8, 4, 'Device Already\nInitialized?', colors['decision'])

    # Already initialized
    already_init = add_node(5, 3, 'Device Already\nConfigured\nProceed to Unlock', colors['io'])

    # Initialize device
    init_device = add_node(11, 3, 'Initialize SED Device\n(sedutil-cli --initialSetup)', colors['process'])

    # Generate HSM key
    gen_key = add_node(8, 1.5, 'Generate Authentication\nKey in YubiHSM 2\n(AES-256)', colors['hsm'])

    # Configure MBR
    config_mbr = add_node(8, 0, 'Configure MBR Shadow\n(setMBRDone on)', colors['process'])

    # Create systemd service
    systemd = add_node(8, -1.5, 'Create Systemd Service\nfor Auto-Unlock', colors['process'])

    # Store configuration
    store_config = add_node(8, -3, 'Store Configuration\n(/etc/yubihsm/sed-ssds/)', colors['io'])

    # Success
    success = add_node(8, -4.5, 'SED SSD Ready\nHardware Encryption\nActive', colors['end'])

    # Connect nodes with arrows
    add_arrow(8, 10.6, 8, 10.1)  # Start to check1
    add_arrow(8, 8.6, 8, 8.6)    # check1 to detect
    add_arrow(8, 7.6, 8, 7.1)    # detect to select_decision

    # Decision branches
    add_arrow(8, 6.1, 5, 6.1, "No")    # No device
    add_arrow(8, 6.1, 11, 6.1, "Yes")  # Device found

    add_arrow(5, 5.1, 5, 4.6)  # No device to error
    add_arrow(11, 5.1, 8, 4.4)  # Device info to init decision

    # Init decision branches
    add_arrow(8, 3.6, 5, 3.4, "Yes")   # Already initialized
    add_arrow(8, 3.6, 11, 3.4, "No")   # Initialize

    add_arrow(5, 2.6, 8, 2.1)  # Already init to gen key
    add_arrow(11, 2.6, 8, 2.1)  # Init device to gen key

    add_arrow(8, 1.1, 8, 0.6)  # Gen key to config MBR
    add_arrow(8, -0.4, 8, -0.9)  # Config MBR to systemd
    add_arrow(8, -1.9, 8, -2.4)  # Systemd to store config
    add_arrow(8, -3.4, 8, -3.9)  # Store config to success

    # Add workflow annotations
    ax.text(2, 10.5, 'Phase 1:\nPrerequisites', fontsize=10, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#e3f2fd"))
    ax.text(2, 7.5, 'Phase 2:\nDevice\nDetection', fontsize=10, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#e3f2fd"))
    ax.text(2, 4.5, 'Phase 3:\nDevice\nSetup', fontsize=10, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#e3f2fd"))
    ax.text(2, 1.5, 'Phase 4:\nHSM\nIntegration', fontsize=10, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#f9f9f9"))
    ax.text(2, -1.5, 'Phase 5:\nAutomation', fontsize=10, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#e3f2fd"))

    plt.tight_layout()
    plt.savefig('sed_ssd_workflow.png', dpi=300, bbox_inches='tight')
    plt.close()

def create_sed_unlock_flowchart():
    """Create SED SSD unlock process flowchart"""

    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 10)
    ax.axis('off')

    ax.set_title('SED SSD Unlock Process with YubiHSM 2', fontsize=16, fontweight='bold', pad=20)

    colors = {
        'start': '#e8f5e9',
        'process': '#e3f2fd',
        'decision': '#fff3e0',
        'hsm': '#f9f9f9',
        'security': '#ffebee',
        'end': '#e8f5e9'
    }

    def add_node(x, y, text, color, width=2.8, height=0.8):
        rect = FancyBboxPatch((x-width/2, y-height/2), width, height,
                            boxstyle="round,pad=0.1", facecolor=color,
                            edgecolor='black', linewidth=1.5)
        ax.add_patch(rect)

        wrapped_text = textwrap.fill(text, width=25)
        ax.text(x, y, wrapped_text, ha='center', va='center',
               fontsize=9, fontweight='bold')
        return rect

    def add_decision(x, y, text, color):
        diamond = mpatches.Polygon([[x, y+0.4], [x+0.8, y], [x, y-0.4], [x-0.8, y]],
                                 facecolor=color, edgecolor='black', linewidth=1.5)
        ax.add_patch(diamond)

        wrapped_text = textwrap.fill(text, width=15)
        ax.text(x, y, wrapped_text, ha='center', va='center',
               fontsize=8, fontweight='bold')
        return diamond

    def add_arrow(start_x, start_y, end_x, end_y, label=""):
        con = ConnectionPatch((start_x, start_y), (end_x, end_y), "data", "data",
                            arrowstyle="->", shrinkA=5, shrinkB=5,
                            mutation_scale=15, fc="black", linewidth=1.5)
        ax.add_patch(con)

        if label:
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom',
                   fontsize=8, bbox=dict(boxstyle="round,pad=0.2", facecolor="white"))

    # System boot
    boot = add_node(7, 9, 'System Boot\n(BIOS/UEFI)', colors['start'])

    # Systemd service start
    systemd_start = add_node(7, 7.5, 'Systemd Service Start\n(yubihsm-sed-unlock)', colors['process'])

    # Check device present
    device_check = add_decision(7, 6, 'SED Device\nPresent?', colors['decision'])

    # Device not present
    device_missing = add_node(4, 5, 'Log Warning\nDevice Not Found\nContinue Boot', colors['security'])

    # Device present - check config
    config_check = add_decision(10, 5, 'Configuration\nExists?', colors['decision'])

    # No config
    no_config = add_node(13, 4, 'ERROR: No SED\nConfiguration Found\nManual Unlock Required', colors['security'])

    # Config exists - connect to HSM
    hsm_connect = add_node(7, 4, 'Connect to YubiHSM 2\n(Authenticate Session)', colors['hsm'])

    # Retrieve key
    get_key = add_node(7, 2.5, 'Retrieve SED Auth Key\nFrom YubiHSM', colors['hsm'])

    # Derive password
    derive_pass = add_node(7, 1, 'Derive SED Password\nFrom HSM Key', colors['process'])

    # Unlock device
    unlock_device = add_node(7, -0.5, 'Unlock SED Device\n(setMBREnable on)', colors['process'])

    # Verify unlock
    verify_unlock = add_decision(7, -2, 'Unlock\nSuccessful?', colors['decision'])

    # Unlock failed
    unlock_failed = add_node(4, -3, 'ERROR: Unlock Failed\nLog Error\nRetry or Manual Unlock', colors['security'])

    # Unlock successful
    unlock_success = add_node(10, -3, 'SED Device Unlocked\nHardware Encryption Active', colors['end'])

    # Continue boot
    continue_boot = add_node(7, -4.5, 'Continue System Boot\nMount Filesystems', colors['process'])

    # Connect arrows
    add_arrow(7, 8.6, 7, 8.1)      # Boot to systemd
    add_arrow(7, 7.1, 7, 6.4)      # Systemd to device check

    # Device check branches
    add_arrow(7, 5.6, 4, 5.4, "No")    # No device
    add_arrow(7, 5.6, 10, 5.4, "Yes")  # Device present

    add_arrow(4, 4.6, 4, 3.6)      # Device missing continue
    add_arrow(10, 4.6, 7, 4.4)     # Config check to HSM connect

    # Config check branches
    add_arrow(10, 4.2, 13, 4.2, "No")  # No config
    add_arrow(10, 4.2, 7, 3.6, "Yes")  # Config exists

    add_arrow(7, 3.6, 7, 3.1)      # HSM connect to get key
    add_arrow(7, 2.1, 7, 1.6)      # Get key to derive pass
    add_arrow(7, 0.6, 7, -0.1)     # Derive to unlock
    add_arrow(7, -0.9, 7, -1.6)    # Unlock to verify

    # Verify branches
    add_arrow(7, -2.4, 4, -2.6, "No")   # Failed
    add_arrow(7, -2.4, 10, -2.6, "Yes") # Success

    add_arrow(4, -3.4, 7, -3.9)     # Failed to continue (with error)
    add_arrow(10, -3.4, 7, -3.9)    # Success to continue

    add_arrow(7, -4.1, 7, -5)       # Continue boot

    # Add timing annotations
    ax.text(1, 8.5, 'Boot Time\nCritical Path', fontsize=9, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.2", facecolor="#ffebee"))
    ax.text(1, 3.5, 'HSM\nCommunication', fontsize=9, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.2", facecolor="#f9f9f9"))
    ax.text(1, 0.5, 'SED\nOperations', fontsize=9, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.2", facecolor="#e3f2fd"))

    plt.tight_layout()
    plt.savefig('sed_ssd_unlock_flow.png', dpi=300, bbox_inches='tight')
    plt.close()

def create_sed_security_flowchart():
    """Create SED SSD security architecture flowchart"""

    fig, ax = plt.subplots(1, 1, figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis('off')

    ax.set_title('SED SSD Security Architecture with YubiHSM 2', fontsize=16, fontweight='bold', pad=20)

    colors = {
        'user': '#e8f5e9',
        'sed': '#e3f2fd',
        'hsm': '#f9f9f9',
        'key': '#fff3e0',
        'encrypt': '#f3e5f5',
        'threat': '#ffebee'
    }

    def add_component(x, y, text, color, width=3, height=1.2):
        rect = FancyBboxPatch((x-width/2, y-height/2), width, height,
                            boxstyle="round,pad=0.2", facecolor=color,
                            edgecolor='black', linewidth=2)
        ax.add_patch(rect)

        wrapped_text = textwrap.fill(text, width=20)
        ax.text(x, y, wrapped_text, ha='center', va='center',
               fontsize=10, fontweight='bold')
        return rect

    def add_arrow(start_x, start_y, end_x, end_y, label="", style="->"):
        con = ConnectionPatch((start_x, start_y), (end_x, end_y), "data", "data",
                            arrowstyle=style, shrinkA=10, shrinkB=10,
                            mutation_scale=15, fc="black", linewidth=2)
        ax.add_patch(con)

        if label:
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom',
                   fontsize=8, fontweight='bold',
                   bbox=dict(boxstyle="round,pad=0.2", facecolor="white"))

    # User/Application Layer
    user = add_component(2, 8, 'User/Application\nAccess Request', colors['user'])

    # SED SSD Hardware
    sed = add_component(8, 8, 'SED SSD Hardware\nTCG Opal 2.0\nAES-256 Encryption', colors['sed'])

    # Authentication Layer
    auth = add_component(14, 8, 'SED Authentication\nPassword Required', colors['key'])

    # YubiHSM 2
    hsm = add_component(8, 5, 'YubiHSM 2\nFIPS 140-2 Level 3\nHardware Security Module', colors['hsm'])

    # Key Storage
    key_store = add_component(4, 5, 'SED Auth Key\nAES-256\nObject ID: 6xxx', colors['key'])

    # Key Derivation
    key_derive = add_component(12, 5, 'Key Derivation\nPBKDF2/SHA-256\nPassword → SED Auth', colors['key'])

    # Encryption Engine
    encrypt = add_component(8, 2, 'SED Encryption Engine\nAES-XTS 256\nHardware Accelerated', colors['encrypt'])

    # Data Storage
    data = add_component(8, 0, 'Encrypted Data\nStorage\nTamper-Evident', colors['encrypt'])

    # Threat vectors
    threat1 = add_component(0, 6, 'Cold Boot\nAttack', colors['threat'])
    threat2 = add_component(16, 6, 'Password\nGuessing', colors['threat'])
    threat3 = add_component(0, 2, 'Physical\nTampering', colors['threat'])
    threat4 = add_component(16, 2, 'Key Extraction\nAttack', colors['threat'])

    # Connect components
    add_arrow(2, 7.4, 8, 8.6, "Read/Write Request")  # User to SED
    add_arrow(8, 7.4, 14, 8.6, "Authentication Required")  # SED to Auth
    add_arrow(14, 7.4, 12, 5.6, "Password Verification")  # Auth to Key Derive
    add_arrow(12, 4.4, 8, 5.6, "Auth Success")  # Key Derive to HSM
    add_arrow(8, 4.4, 8, 2.6, "Unlock Command")  # HSM to Encrypt Engine
    add_arrow(8, 1.4, 8, 0.6, "Encrypt/Decrypt Data")  # Encrypt to Data

    # Key management
    add_arrow(4, 4.4, 8, 5.6, "Key Retrieval")  # Key Store to HSM
    add_arrow(8, 4.4, 12, 5.6, "Key for Derivation")  # HSM to Key Derive

    # Threat mitigation arrows (blocking)
    add_arrow(0, 5.4, 4, 5.6, "BLOCKED", "->")  # Threat1 blocked by HSM
    add_arrow(16, 5.4, 12, 5.6, "BLOCKED", "->")  # Threat2 blocked by HSM
    add_arrow(0, 1.4, 8, 2.6, "BLOCKED", "->")  # Threat3 blocked by SED
    add_arrow(16, 1.4, 8, 2.6, "BLOCKED", "->")  # Threat4 blocked by HSM

    # Security annotations
    ax.text(1, 9.2, 'Application Layer', fontsize=11, fontweight='bold')
    ax.text(8, 9.2, 'Hardware Security Layer', fontsize=11, fontweight='bold')
    ax.text(15, 9.2, 'Authentication Layer', fontsize=11, fontweight='bold')

    ax.text(1, 3.8, 'Threat Mitigation', fontsize=11, fontweight='bold',
           bbox=dict(boxstyle="round,pad=0.3", facecolor="#ffebee"))

    # Security features
    ax.text(8, -1, 'Security Features: FIPS 140-2 Level 3 | TCG Opal 2.0 | AES-256-XTS | Tamper-Evident | Hardware Isolation',
           fontsize=9, ha='center', style='italic',
           bbox=dict(boxstyle="round,pad=0.5", facecolor="#e8f5e9"))

    plt.tight_layout()
    plt.savefig('sed_ssd_security_architecture.png', dpi=300, bbox_inches='tight')
    plt.close()

def main():
    """Generate all SED SSD flowcharts"""
    print("Generating SED SSD workflow flowcharts...")

    create_sed_workflow_flowchart()
    print("✓ Created sed_ssd_workflow.png")

    create_sed_unlock_flowchart()
    print("✓ Created sed_ssd_unlock_flow.png")

    create_sed_security_flowchart()
    print("✓ Created sed_ssd_security_architecture.png")

    print("\nAll flowcharts generated successfully!")
    print("Files created:")
    print("  - sed_ssd_workflow.png")
    print("  - sed_ssd_unlock_flow.png")
    print("  - sed_ssd_security_architecture.png")

if __name__ == "__main__":
    main()