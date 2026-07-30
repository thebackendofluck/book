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
HSM Comparison Visualization Tool
Generates architecture diagrams and performance charts for YubiHSM 2 vs Nitrokey HSM 2
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

def create_architecture_diagram():
    """Create comprehensive architecture comparison diagram"""
    
    fig, axes = plt.subplots(2, 2, figsize=(20, 16))
    fig.suptitle('HSM Architecture Comparison', fontsize=20, fontweight='bold')
    
    # YubiHSM 2 Architecture
    ax1 = axes[0, 0]
    ax1.set_title('YubiHSM 2 Architecture', fontsize=16, fontweight='bold')
    ax1.set_xlim(0, 10)
    ax1.set_ylim(0, 10)
    ax1.axis('off')
    
    # Application Layer
    ax1.add_patch(FancyBboxPatch((1, 8), 8, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightblue', 
                                  edgecolor='black'))
    ax1.text(5, 8.75, 'Applications\n(TLS, PKI, Code Signing)', 
             ha='center', va='center', fontsize=10)
    
    # YubiHSM Connector
    ax1.add_patch(FancyBboxPatch((2, 6), 6, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightgreen', 
                                  edgecolor='black'))
    ax1.text(5, 6.75, 'YubiHSM Connector\n(HTTP Proxy)', 
             ha='center', va='center', fontsize=10)
    
    # YubiHSM Device
    ax1.add_patch(FancyBboxPatch((3, 3.5), 4, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='yellow', 
                                  edgecolor='black'))
    ax1.text(5, 4.25, 'YubiHSM 2 Device\n(USB Nano)', 
             ha='center', va='center', fontsize=10)
    
    # Secure Element
    ax1.add_patch(FancyBboxPatch((3.5, 1), 3, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='salmon', 
                                  edgecolor='black'))
    ax1.text(5, 1.75, 'Secure Element\n(Crypto Operations)', 
             ha='center', va='center', fontsize=10)
    
    # Add arrows
    ax1.arrow(5, 7.5, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    ax1.arrow(5, 5.5, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    ax1.arrow(5, 3, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    
    # Nitrokey HSM 2 Architecture
    ax2 = axes[0, 1]
    ax2.set_title('Nitrokey HSM 2 Architecture', fontsize=16, fontweight='bold')
    ax2.set_xlim(0, 10)
    ax2.set_ylim(0, 10)
    ax2.axis('off')
    
    # Application Layer
    ax2.add_patch(FancyBboxPatch((1, 8), 8, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightblue', 
                                  edgecolor='black'))
    ax2.text(5, 8.75, 'Applications\n(OpenSSL, Java, Python)', 
             ha='center', va='center', fontsize=10)
    
    # PKCS#11 Interface
    ax2.add_patch(FancyBboxPatch((2, 6), 6, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightgreen', 
                                  edgecolor='black'))
    ax2.text(5, 6.75, 'PKCS#11 Interface\n(OpenSC)', 
             ha='center', va='center', fontsize=10)
    
    # Smart Card
    ax2.add_patch(FancyBboxPatch((3, 3.5), 4, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='yellow', 
                                  edgecolor='black'))
    ax2.text(5, 4.25, 'Smart Card\n(JCOP 3 P60)', 
             ha='center', va='center', fontsize=10)
    
    # Crypto Processor
    ax2.add_patch(FancyBboxPatch((3.5, 1), 3, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='salmon', 
                                  edgecolor='black'))
    ax2.text(5, 1.75, 'Crypto Processor\n(CC EAL 5+)', 
             ha='center', va='center', fontsize=10)
    
    # Add arrows
    ax2.arrow(5, 7.5, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    ax2.arrow(5, 5.5, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    ax2.arrow(5, 3, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    
    # Blockchain HSM Architecture
    ax3 = axes[1, 0]
    ax3.set_title('Blockchain-based HSM Architecture', fontsize=16, fontweight='bold')
    ax3.set_xlim(0, 10)
    ax3.set_ylim(0, 10)
    ax3.axis('off')
    
    # Application Layer
    ax3.add_patch(FancyBboxPatch((1, 8), 8, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightblue', 
                                  edgecolor='black'))
    ax3.text(5, 8.75, 'DApp / Application Layer', 
             ha='center', va='center', fontsize=10)
    
    # Smart Contract
    ax3.add_patch(FancyBboxPatch((2, 6), 6, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightgreen', 
                                  edgecolor='black'))
    ax3.text(5, 6.75, 'Smart Contracts\n(Access Control)', 
             ha='center', va='center', fontsize=10)
    
    # Validator Network
    for i in range(3):
        x = 2 + i * 2.5
        ax3.add_patch(FancyBboxPatch((x, 3.5), 1.8, 1.2, 
                                      boxstyle="round,pad=0.1",
                                      facecolor='yellow', 
                                      edgecolor='black'))
        ax3.text(x + 0.9, 4.1, f'Validator\n{i+1}', 
                 ha='center', va='center', fontsize=9)
    
    # Threshold Signatures
    ax3.add_patch(FancyBboxPatch((3, 1), 4, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='salmon', 
                                  edgecolor='black'))
    ax3.text(5, 1.75, 'Threshold Signatures\n(3-of-5 MPC)', 
             ha='center', va='center', fontsize=10)
    
    # Add arrows
    ax3.arrow(5, 7.5, 0, -0.7, head_width=0.2, head_length=0.1, fc='black')
    for i in range(3):
        x = 2.9 + i * 2.5
        ax3.arrow(x, 3.3, 0, -1.0, head_width=0.15, head_length=0.1, fc='black')
    
    # Hybrid Architecture
    ax4 = axes[1, 1]
    ax4.set_title('Hybrid HSM Architecture (Recommended)', fontsize=16, fontweight='bold')
    ax4.set_xlim(0, 10)
    ax4.set_ylim(0, 10)
    ax4.axis('off')
    
    # Application Layer
    ax4.add_patch(FancyBboxPatch((1, 8), 8, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightblue', 
                                  edgecolor='black'))
    ax4.text(5, 8.75, 'Enterprise Applications', 
             ha='center', va='center', fontsize=10)
    
    # Orchestration Layer
    ax4.add_patch(FancyBboxPatch((1.5, 6), 7, 1.2, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='purple', 
                                  edgecolor='black', alpha=0.3))
    ax4.text(5, 6.6, 'Orchestration Layer', 
             ha='center', va='center', fontsize=10)
    
    # YubiHSM for Performance
    ax4.add_patch(FancyBboxPatch((1, 3.5), 3, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='yellow', 
                                  edgecolor='black'))
    ax4.text(2.5, 4.25, 'YubiHSM 2\n(Performance)', 
             ha='center', va='center', fontsize=9)
    
    # Nitrokey for M-of-N
    ax4.add_patch(FancyBboxPatch((4.5, 3.5), 3, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='lightgreen', 
                                  edgecolor='black'))
    ax4.text(6, 4.25, 'Nitrokey HSM\n(M-of-N)', 
             ha='center', va='center', fontsize=9)
    
    # Blockchain for Audit
    ax4.add_patch(FancyBboxPatch((2.5, 1), 5, 1.5, 
                                  boxstyle="round,pad=0.1",
                                  facecolor='salmon', 
                                  edgecolor='black'))
    ax4.text(5, 1.75, 'Blockchain\n(Immutable Audit Trail)', 
             ha='center', va='center', fontsize=10)
    
    # Add connections
    ax4.arrow(5, 7.5, -2, -2, head_width=0.15, head_length=0.1, fc='black')
    ax4.arrow(5, 7.5, 1, -2, head_width=0.15, head_length=0.1, fc='black')
    ax4.arrow(2.5, 3.3, 2, -1.3, head_width=0.15, head_length=0.1, fc='black')
    ax4.arrow(6, 3.3, -1, -1.3, head_width=0.15, head_length=0.1, fc='black')
    
    plt.tight_layout()
    plt.savefig('./hsm_architecture_comparison.png', dpi=300, bbox_inches='tight')
    plt.show()

def create_performance_charts():
    """Create performance comparison charts"""
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle('HSM Performance Comparison', fontsize=18, fontweight='bold')
    
    # RSA Performance Comparison
    ax1 = axes[0, 0]
    hsms = ['YubiHSM 2', 'Nitrokey\nHSM 2', 'Cloud HSM', 'Blockchain\nTSS']
    rsa2048 = [7500, 120, 10000, 100]
    rsa4096 = [630, 30, 2500, 50]
    
    x = np.arange(len(hsms))
    width = 0.35
    
    bars1 = ax1.bar(x - width/2, rsa2048, width, label='RSA-2048', color='skyblue')
    bars2 = ax1.bar(x + width/2, rsa4096, width, label='RSA-4096', color='lightcoral')
    
    ax1.set_ylabel('Operations per Second')
    ax1.set_title('RSA Signing Performance')
    ax1.set_xticks(x)
    ax1.set_xticklabels(hsms)
    ax1.legend()
    ax1.set_yscale('log')
    ax1.grid(True, alpha=0.3)
    
    # Add value labels on bars
    for bar in bars1:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height*1.05,
                f'{int(height)}', ha='center', va='bottom', fontsize=9)
    for bar in bars2:
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height*1.05,
                f'{int(height)}', ha='center', va='bottom', fontsize=9)
    
    # ECC Performance Comparison
    ax2 = axes[0, 1]
    ecc256 = [4600, 360, 8000, 200]
    
    bars = ax2.bar(hsms, ecc256, color='lightgreen')
    ax2.set_ylabel('Operations per Second')
    ax2.set_title('ECC P-256 Signing Performance')
    ax2.set_yscale('log')
    ax2.grid(True, alpha=0.3)
    
    for bar in bars:
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height*1.05,
                f'{int(height)}', ha='center', va='bottom', fontsize=9)
    
    # Cost Comparison
    ax3 = axes[1, 0]
    solutions = ['YubiHSM 2\n(3 units)', 'Nitrokey\n(5 units)', 'CloudHSM\n(3 years)', 'Network HSM']
    costs = [3950, 4700, 43048, 44000]
    colors = ['gold', 'lightgreen', 'lightcoral', 'salmon']
    
    bars = ax3.bar(solutions, costs, color=colors)
    ax3.set_ylabel('Total Cost (USD)')
    ax3.set_title('3-Year Total Cost of Ownership')
    ax3.grid(True, alpha=0.3, axis='y')
    
    for bar in bars:
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width()/2., height + 500,
                f'${int(height):,}', ha='center', va='bottom', fontsize=10)
    
    # Feature Comparison Radar Chart
    ax4 = axes[1, 1]
    
    categories = ['Performance', 'Security', 'Audit Trail', 'Cost-Effective', 'Compliance']
    N = len(categories)
    
    # Create angles for radar chart
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]
    
    # Data for each HSM
    yubihsm_scores = [10, 8, 6, 8, 9]
    nitrokey_scores = [3, 8, 6, 9, 7]
    blockchain_scores = [2, 7, 10, 4, 5]
    hybrid_scores = [9, 9, 10, 7, 9]
    
    # Close the plot
    yubihsm_scores += yubihsm_scores[:1]
    nitrokey_scores += nitrokey_scores[:1]
    blockchain_scores += blockchain_scores[:1]
    hybrid_scores += hybrid_scores[:1]
    
    # Clear the subplot and create polar projection
    ax4.remove()
    ax4 = fig.add_subplot(224, projection='polar')
    
    # Plot data
    ax4.plot(angles, yubihsm_scores, 'o-', linewidth=2, label='YubiHSM 2', color='blue')
    ax4.fill(angles, yubihsm_scores, alpha=0.25, color='blue')
    
    ax4.plot(angles, nitrokey_scores, 'o-', linewidth=2, label='Nitrokey HSM 2', color='green')
    ax4.fill(angles, nitrokey_scores, alpha=0.25, color='green')
    
    ax4.plot(angles, blockchain_scores, 'o-', linewidth=2, label='Blockchain', color='orange')
    ax4.fill(angles, blockchain_scores, alpha=0.25, color='orange')
    
    ax4.plot(angles, hybrid_scores, 'o-', linewidth=2, label='Hybrid', color='red')
    ax4.fill(angles, hybrid_scores, alpha=0.25, color='red')
    
    # Set labels
    ax4.set_xticks(angles[:-1])
    ax4.set_xticklabels(categories)
    ax4.set_ylim(0, 10)
    ax4.set_title('Feature Comparison (10 = Best)', y=1.08)
    ax4.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1))
    ax4.grid(True)
    
    plt.tight_layout()
    plt.savefig('./hsm_performance_comparison.png', dpi=300, bbox_inches='tight')
    plt.show()

def create_decision_flowchart():
    """Create decision tree flowchart"""
    
    fig, ax = plt.subplots(figsize=(14, 10))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 10)
    ax.axis('off')
    ax.set_title('HSM Selection Decision Tree', fontsize=18, fontweight='bold', pad=20)
    
    # Define decision nodes
    def add_decision_node(x, y, text, color='lightblue'):
        box = FancyBboxPatch((x-1.2, y-0.3), 2.4, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=10, fontweight='bold')
    
    def add_outcome_node(x, y, text, color='lightgreen'):
        box = FancyBboxPatch((x-1.2, y-0.3), 2.4, 0.6,
                             boxstyle="round,pad=0.05",
                             facecolor=color, edgecolor='black', linewidth=2)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=10)
    
    def add_arrow(x1, y1, x2, y2, label='', style='->'):
        arrow = FancyArrowPatch((x1, y1), (x2, y2),
                               arrowstyle=style, linewidth=1.5,
                               edgecolor='black', facecolor='black')
        ax.add_patch(arrow)
        if label:
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            ax.text(mid_x, mid_y + 0.1, label, ha='center', va='bottom', fontsize=9)
    
    # Root node
    add_decision_node(7, 9, 'HSM Selection', 'yellow')
    
    # Level 1
    add_decision_node(3.5, 7.5, 'Performance\n> 1000 ops/s?')
    add_decision_node(10.5, 7.5, 'Open Source\nRequired?')
    add_arrow(7, 8.7, 3.5, 7.8, 'Start')
    add_arrow(7, 8.7, 10.5, 7.8)
    
    # Level 2 - Performance branch
    add_decision_node(2, 5.5, 'Budget\n> $10k?')
    add_outcome_node(5, 5.5, 'YubiHSM 2', 'lightgreen')
    add_arrow(3.5, 7.2, 2, 5.8, 'Yes')
    add_arrow(3.5, 7.2, 5, 5.8, 'No')
    
    # Level 2 - Open Source branch
    add_outcome_node(9, 5.5, 'Nitrokey\nHSM 2', 'lightgreen')
    add_decision_node(12, 5.5, 'Distributed\nTrust?')
    add_arrow(10.5, 7.2, 9, 5.8, 'Yes')
    add_arrow(10.5, 7.2, 12, 5.8, 'No')
    
    # Level 3
    add_outcome_node(1, 3.5, 'Cloud HSM\nor Thales', 'lightcoral')
    add_outcome_node(3, 3.5, 'YubiHSM 2', 'lightgreen')
    add_arrow(2, 5.2, 1, 3.8, 'Yes')
    add_arrow(2, 5.2, 3, 3.8, 'No')
    
    # Distributed Trust branch
    add_decision_node(11, 3.5, 'Compliance\nRequired?')
    add_outcome_node(13, 3.5, 'YubiHSM 2\nor Nitrokey', 'lightgreen')
    add_arrow(12, 5.2, 11, 3.8, 'Yes')
    add_arrow(12, 5.2, 13, 3.8, 'No')
    
    # Final level
    add_outcome_node(10, 1.5, 'Hybrid:\nHSM + Blockchain', 'gold')
    add_outcome_node(12, 1.5, 'Pure\nBlockchain', 'lightcoral')
    add_arrow(11, 3.2, 10, 1.8, 'Yes')
    add_arrow(11, 3.2, 12, 1.8, 'No')
    
    # Add legend
    legend_x, legend_y = 0.5, 0.5
    ax.text(legend_x, legend_y + 1, 'Legend:', fontsize=10, fontweight='bold')
    ax.add_patch(FancyBboxPatch((legend_x, legend_y + 0.5), 0.8, 0.3,
                                boxstyle="round,pad=0.02",
                                facecolor='lightblue', edgecolor='black'))
    ax.text(legend_x + 1, legend_y + 0.65, 'Decision', fontsize=9)
    
    ax.add_patch(FancyBboxPatch((legend_x, legend_y), 0.8, 0.3,
                                boxstyle="round,pad=0.02",
                                facecolor='lightgreen', edgecolor='black'))
    ax.text(legend_x + 1, legend_y + 0.15, 'Recommendation', fontsize=9)
    
    plt.tight_layout()
    plt.savefig('./hsm_decision_tree.png', dpi=300, bbox_inches='tight')
    plt.show()

def main():
    """Generate all visualization diagrams"""
    print("Generating HSM Comparison Visualizations...")
    
    print("1. Creating Architecture Diagrams...")
    create_architecture_diagram()
    
    print("2. Creating Performance Charts...")
    create_performance_charts()
    
    print("3. Creating Decision Flowchart...")
    create_decision_flowchart()
    
    print("\nAll visualizations have been saved to:")
    print("  - ./hsm_architecture_comparison.png")
    print("  - ./hsm_performance_comparison.png")
    print("  - ./hsm_decision_tree.png")

if __name__ == "__main__":
    main()
