#!/usr/bin/env python3
"""
visualize.py
------------
Master visualization entrypoint for Structured-ASIC.

This thin wrapper delegates to the visualization package.

Usage:
    python visualize.py --design 6502
    python visualize.py --design 6502 --only layout density
    python visualize.py --design 6502 --skip congestion
    python visualize.py --list

Outputs (saved under build/<design>/):
    <design>_layout.png         Fabric ground-truth layout
    <design>_density.png        Placement density heatmap
    <design>_net_length.png     Net HPWL histogram
    <design>_congestion.png     Congestion heatmap
    <design>_slack.png          Slack histogram
    <design>_critical_path.png  Critical path overlay
    <design>_cts_tree.png       CTS tree overlay
"""

from visualization import main

if __name__ == "__main__":
    main()
