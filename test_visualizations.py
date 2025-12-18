#!/usr/bin/env python3
"""
Test script to validate visualization capabilities for Phase 3 requirements.

Requirements:
1. Layout visualization
2. Congestion Heatmap (from _congestion.rpt)
3. Slack Histogram (from _setup.rpt)
4. Critical Path Overlay (from _setup.rpt)

This script tests what currently works and identifies missing capabilities.
"""

import os
import sys
from visualize import (
    plot_fabric_ground_truth,
    generate_congestion_heatmap_from_report,
    parse_setup_report_for_slacks,
    plot_slack_histogram,
    parse_setup_report_for_worst_path,
    draw_critical_path_overlay,
    _collect_all_fabric_cell_names,
    _ensure_build_dir
)
from build_fabric_db import build_fabric_db


def test_visualizations(design='arith'):
    """Test all visualization capabilities on the arith design."""
    
    print(f"\n{'='*60}")
    print(f"Testing Visualization Capabilities for: {design}")
    print(f"{'='*60}\n")
    
    # Setup paths
    build_dir = _ensure_build_dir(design)
    fabric_db = build_fabric_db("fabric/fabric_cells.yaml", "fabric/pins.yaml", "fabric/fabric.yaml")
    
    results = {
        'layout': False,
        'congestion': False,
        'slack_histogram': False,
        'critical_path': False
    }
    
    # 1. Layout Visualization
    print("1. Testing Layout Visualization...")
    try:
        layout_out = os.path.join(build_dir, f"{design}_layout.png")
        plot_fabric_ground_truth(fabric_db, show=False, savepath=layout_out)
        if os.path.exists(layout_out):
            print(f"   ✓ SUCCESS: Layout saved to {layout_out}")
            results['layout'] = True
        else:
            print(f"   ✗ FAILED: Layout file not created")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
    
    # 2. Congestion Heatmap
    print("\n2. Testing Congestion Heatmap...")
    congestion_rpt = os.path.join(build_dir, f"{design}_congestion.rpt")
    if os.path.exists(congestion_rpt):
        try:
            cong_out = os.path.join(build_dir, f"{design}_congestion.png")
            generate_congestion_heatmap_from_report(congestion_rpt, fabric_db, cong_out)
            if os.path.exists(cong_out):
                print(f"   ✓ SUCCESS: Congestion heatmap saved to {cong_out}")
                results['congestion'] = True
            else:
                print(f"   ✗ FAILED: Congestion heatmap not created")
        except Exception as e:
            print(f"   ✗ FAILED: {e}")
    else:
        print(f"   ✗ MISSING: {congestion_rpt} not found")
        print(f"   → Need to generate congestion report from routing")
    
    # 3. Slack Histogram
    print("\n3. Testing Slack Histogram...")
    setup_rpt = os.path.join(build_dir, f"{design}_setup_timing.rpt")
    if os.path.exists(setup_rpt):
        try:
            slacks = parse_setup_report_for_slacks(setup_rpt)
            if slacks:
                slack_out = os.path.join(build_dir, f"{design}_slack.png")
                plot_slack_histogram(slacks, slack_out)
                if os.path.exists(slack_out):
                    print(f"   ✓ SUCCESS: Slack histogram saved to {slack_out}")
                    print(f"   → Parsed {len(slacks)} slack values")
                    print(f"   → Worst slack: {min(slacks):.3f} ns")
                    print(f"   → Best slack: {max(slacks):.3f} ns")
                    results['slack_histogram'] = True
                else:
                    print(f"   ✗ FAILED: Slack histogram not created")
            else:
                print(f"   ✗ FAILED: No slacks parsed from {setup_rpt}")
                print(f"   → Parser may need adjustment for report format")
        except Exception as e:
            print(f"   ✗ FAILED: {e}")
    else:
        print(f"   ✗ MISSING: {setup_rpt} not found")
    
    # 4. Critical Path Overlay
    print("\n4. Testing Critical Path Overlay...")
    if os.path.exists(setup_rpt):
        try:
            fabric_names = _collect_all_fabric_cell_names(fabric_db)
            path_cells = parse_setup_report_for_worst_path(setup_rpt, fabric_names)
            if path_cells:
                crit_out = os.path.join(build_dir, f"{design}_critical_path.png")
                draw_critical_path_overlay(fabric_db, path_cells, crit_out)
                if os.path.exists(crit_out):
                    print(f"   ✓ SUCCESS: Critical path overlay saved to {crit_out}")
                    print(f"   → Path contains {len(path_cells)} cells")
                    print(f"   → First 5 cells: {path_cells[:5]}")
                    results['critical_path'] = True
                else:
                    print(f"   ✗ FAILED: Critical path overlay not created")
            else:
                print(f"   ✗ FAILED: No critical path cells parsed")
                print(f"   → Parser may need adjustment for report format")
        except Exception as e:
            print(f"   ✗ FAILED: {e}")
    else:
        print(f"   ✗ MISSING: {setup_rpt} not found")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(results.values())
    total = len(results)
    print(f"Tests Passed: {passed}/{total}\n")
    
    for test, status in results.items():
        symbol = "✓" if status else "✗"
        print(f"  {symbol} {test.replace('_', ' ').title()}")
    
    print(f"\n{'='*60}")
    print("MISSING DELIVERABLES")
    print(f"{'='*60}")
    
    missing_files = []
    
    # Check for required report files
    required_reports = [
        f"{design}_congestion.rpt",
        f"{design}_setup.rpt"  # Alternative name
    ]
    
    for rpt in required_reports:
        path = os.path.join(build_dir, rpt)
        if not os.path.exists(path):
            missing_files.append(f"  • {rpt} - needed for congestion/STA visualization")
    
    # Check for output visualizations
    required_outputs = [
        (f"{design}_layout.png", "Layout visualization"),
        (f"{design}_congestion.png", "Congestion heatmap"),
        (f"{design}_slack.png", "Slack histogram"),
        (f"{design}_critical_path.png", "Critical path overlay")
    ]
    
    for filename, description in required_outputs:
        path = os.path.join(build_dir, filename)
        if not os.path.exists(path):
            missing_files.append(f"  • {filename} - {description}")
    
    if missing_files:
        print("\nMissing files:")
        for f in missing_files:
            print(f)
    else:
        print("\n✓ All required files present!")
    
    return results


if __name__ == "__main__":
    design = sys.argv[1] if len(sys.argv) > 1 else 'arith'
    test_visualizations(design)
