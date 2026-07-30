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

"""
Database TDE Flowchart Generator
Creates flowcharts for PostgreSQL and MariaDB TDE processes with YubiHSM integration
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

def create_postgresql_tde_flowchart():
    """Create PostgreSQL TDE flowchart with pg_tde extension"""

    fig, ax = plt.subplots(figsize=(16, 14))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 14)
    ax.axis('off')
    ax.set_title('PostgreSQL TDE with pg_tde Extension - Process Flow', fontsize=18, fontweight='bold', pad=20)

    def add_process_node(x, y, text, color='lightblue'):
        box = FancyBboxPatch((x-1.5, y-0.4), 3, 0.8,
                             boxstyle="round,pad=0.1",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9, fontweight='bold')

    def add_decision_node(x, y, text, color='yellow'):
        box = FancyBboxPatch((x-1.2, y-0.3), 2.4, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9, fontweight='bold')

    def add_io_node(x, y, text, color='lightgreen'):
        box = FancyBboxPatch((x-1.5, y-0.3), 3, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9)

    def add_arrow(x1, y1, x2, y2, label='', style='->'):
        arrow = FancyArrowPatch((x1, y1), (x2, y2),
                               arrowstyle=style, linewidth=1.5,
                               edgecolor='black', facecolor='black')
        ax.add_patch(arrow)
        if label:
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom', fontsize=8)

    # Start
    add_io_node(8, 13, 'Start TDE Setup', 'lightgray')

    # Prerequisites Check
    add_process_node(8, 11.5, 'Check Prerequisites\n(YubiHSM, PostgreSQL 17+, pg_tde)', 'lightblue')

    # Hardware Check
    add_decision_node(8, 10, 'CPU has AES-NI\nSupport?', 'yellow')

    # Extension Installation
    add_process_node(8, 8.5, 'Install pg_tde Extension\nin PostgreSQL Cluster', 'lightcyan')

    # Generate Key
    add_process_node(8, 7, 'Generate AES-256 Key\nin YubiHSM 2', 'lightcyan')

    # Create Key Fetch Script
    add_process_node(8, 5.5, 'Create Key Fetch Script\n(Python + YubiHSM API)', 'lightcyan')

    # Test Script
    add_decision_node(8, 4, 'Script Test\nSuccessful?', 'yellow')

    # Backup Existing Data
    add_process_node(5, 2.5, 'Backup Existing\nPostgreSQL Data', 'orange')

    # Configure pg_tde
    add_process_node(11, 2.5, 'Configure pg_tde Extension\nwith Key Provider', 'lightgreen')

    # Enable TDE for Databases
    add_process_node(8, 1, 'Enable pg_tde for\nTarget Databases', 'lightgreen')

    # Start Service
    add_process_node(8, -0.5, 'Start PostgreSQL\nService', 'lightgreen')

    # Verify TDE
    add_decision_node(8, -2, 'TDE Verification\nSuccessful?', 'yellow')

    # Success
    add_io_node(5, -3.5, 'TDE Setup Complete\nReady for Production', 'lightgreen')

    # Failure
    add_io_node(11, -3.5, 'Setup Failed\nCheck Logs', 'red')

    # Warning for no AES-NI
    add_io_node(2, 8.5, 'WARNING: AES-NI Required\nfor Good Performance', 'red')

    # Arrows
    add_arrow(8, 12.7, 8, 11.9)
    add_arrow(8, 11.1, 8, 10.3)
    add_arrow(8, 9.7, 8, 8.9, 'Yes')
    add_arrow(8, 9.7, 2, 8.9, 'No')
    add_arrow(8, 8.1, 8, 7.4)
    add_arrow(8, 6.6, 8, 5.9)
    add_arrow(8, 5.1, 8, 4.3)
    add_arrow(8, 3.7, 5, 2.9, 'No')
    add_arrow(8, 3.7, 11, 2.9, 'Yes')
    add_arrow(5, 2.1, 8, 1.4)
    add_arrow(11, 2.1, 8, 1.4)
    add_arrow(8, 0.6, 8, -0.1)
    add_arrow(8, -0.9, 8, -1.7)
    add_arrow(8, -2.3, 5, -3.1, 'Yes')
    add_arrow(8, -2.3, 11, -3.1, 'No')

    plt.tight_layout()
    plt.savefig('postgresql_tde_flowchart.png', dpi=300, bbox_inches='tight')
    plt.show()

def create_mariadb_tde_flowchart():
    """Create MariaDB TDE flowchart with native implementation"""

    fig, ax = plt.subplots(figsize=(16, 12))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12)
    ax.axis('off')
    ax.set_title('MariaDB TDE with YubiHSM 2 - Process Flow', fontsize=18, fontweight='bold', pad=20)

    def add_process_node(x, y, text, color='lightblue'):
        box = FancyBboxPatch((x-1.5, y-0.4), 3, 0.8,
                             boxstyle="round,pad=0.1",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9, fontweight='bold')

    def add_decision_node(x, y, text, color='yellow'):
        box = FancyBboxPatch((x-1.2, y-0.3), 2.4, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9, fontweight='bold')

    def add_io_node(x, y, text, color='lightgreen'):
        box = FancyBboxPatch((x-1.5, y-0.3), 3, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=9)

    def add_arrow(x1, y1, x2, y2, label='', style='->'):
        arrow = FancyArrowPatch((x1, y1), (x2, y2),
                               arrowstyle=style, linewidth=1.5,
                               edgecolor='black', facecolor='black')
        ax.add_patch(arrow)
        if label:
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom', fontsize=8)

    # Start
    add_io_node(8, 11, 'Start TDE Setup', 'lightgray')

    # Prerequisites Check
    add_process_node(8, 9.5, 'Check Prerequisites\n(YubiHSM, MariaDB 10.11+, Tools)', 'lightblue')

    # Generate Key
    add_process_node(8, 8, 'Generate AES-256 Key\nin YubiHSM 2', 'lightcyan')

    # Create Key Fetch Script
    add_process_node(8, 6.5, 'Create Key Fetch Script\n(Python + YubiHSM API)', 'lightcyan')

    # Test Script
    add_decision_node(8, 5, 'Script Test\nSuccessful?', 'yellow')

    # Backup Existing Data
    add_process_node(5, 3.5, 'Backup Existing\nMariaDB Data', 'orange')

    # Generate Keys File
    add_process_node(11, 3.5, 'Generate Encryption\nKeys File with Versioning', 'lightgreen')

    # Configure MariaDB
    add_process_node(8, 2, 'Configure MariaDB\n(InnoDB, Aria, SSL)', 'lightgreen')

    # Start Service
    add_process_node(8, 0.5, 'Start MariaDB\nService', 'lightgreen')

    # Verify TDE
    add_decision_node(8, -1, 'TDE Verification\nSuccessful?', 'yellow')

    # Success
    add_io_node(5, -2.5, 'TDE Setup Complete\nReady for Production', 'lightgreen')

    # Failure
    add_io_node(11, -2.5, 'Setup Failed\nCheck Logs', 'red')

    # Arrows
    add_arrow(8, 10.7, 8, 9.9)
    add_arrow(8, 9.1, 8, 8.4)
    add_arrow(8, 7.6, 8, 6.9)
    add_arrow(8, 6.1, 8, 5.3)
    add_arrow(8, 4.7, 5, 3.9, 'No')
    add_arrow(8, 4.7, 11, 3.9, 'Yes')
    add_arrow(5, 3.1, 8, 2.4)
    add_arrow(11, 3.1, 8, 2.4)
    add_arrow(8, 1.6, 8, 0.9)
    add_arrow(8, 0.1, 8, -0.7)
    add_arrow(8, -1.3, 5, -2.1, 'Yes')
    add_arrow(8, -1.3, 11, -2.1, 'No')

    plt.tight_layout()
    plt.savefig('mariadb_tde_flowchart.png', dpi=300, bbox_inches='tight')
    plt.show()

def create_comparison_table():
    """Create comparative table of PostgreSQL vs MariaDB TDE"""

    fig, ax = plt.subplots(figsize=(14, 8))
    ax.axis('off')
    ax.set_title('PostgreSQL vs MariaDB TDE Comparison', fontsize=16, fontweight='bold', pad=20)

    # Table data
    columns = ['Feature', 'PostgreSQL TDE', 'MariaDB TDE', 'Notes']
    rows = [
        ['Encryption Method', 'Cluster-level\n(AES-256)', 'Table-level\n(AES-256)', 'PostgreSQL encrypts entire cluster'],
        ['Key Management', 'External command\nscript', 'File-based keys\nwith plugin', 'Both support external key management'],
        ['Key Storage', 'YubiHSM 2', 'YubiHSM 2', 'Same HSM integration'],
        ['Performance Impact', 'Low (5-10%)', 'Low (3-8%)', 'MariaDB generally faster'],
        ['Backup Encryption', 'Automatic', 'Manual config', 'PostgreSQL has better integration'],
        ['Key Rotation', 'Manual process', 'Versioned keys', 'MariaDB supports online rotation'],
        ['Storage Overhead', 'Minimal', 'Minimal', 'Similar overhead'],
        ['Compliance', 'FIPS 140-2', 'FIPS 140-2', 'Both support compliance requirements'],
        ['Setup Complexity', 'Medium', 'Medium', 'Similar complexity'],
        ['Monitoring', 'Built-in views', 'Information schema', 'Both provide monitoring capabilities']
    ]

    # Create table
    table = ax.table(cellText=rows, colLabels=columns, loc='center',
                    cellLoc='left', colColours=['lightblue']*4)

    # Style the table
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.5)

    # Color cells based on preference
    for i, row in enumerate(rows):
        # PostgreSQL advantages
        if row[1] in ['Cluster-level\n(AES-256)', 'Automatic', 'Built-in views']:
            table[(i+1, 1)].set_facecolor('lightgreen')
        # MariaDB advantages
        if row[2] in ['Table-level\n(AES-256)', 'Versioned keys', 'Low (3-8%)']:
            table[(i+1, 2)].set_facecolor('lightgreen')

    plt.tight_layout()
    plt.savefig('database_tde_comparison.png', dpi=300, bbox_inches='tight')
    plt.show()

def main():
    """Generate all database TDE flowcharts and comparisons"""
    print("Generating Database TDE Flowcharts and Comparisons...")

    print("1. Creating PostgreSQL TDE Flowchart...")
    create_postgresql_tde_flowchart()

    print("2. Creating MariaDB TDE Flowchart...")
    create_mariadb_tde_flowchart()

    print("3. Creating Comparison Table...")
    create_comparison_table()

    print("\nAll visualizations have been saved:")
    print("  - postgresql_tde_flowchart.png")
    print("  - mariadb_tde_flowchart.png")
    print("  - database_tde_comparison.png")

if __name__ == "__main__":
    main()