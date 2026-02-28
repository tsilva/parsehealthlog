#!/usr/bin/env python3
"""
Health Log Parser Quality Review - Master Orchestrator

Coordinates all 7 phases of the quality review process and generates comprehensive reports.

Usage:
    python run_quality_review.py --tiago-path /path/to/tiago/output --cristina-path /path/to/cristina/output --output-dir /path/to/reports

Phases:
    1. Validation - Run validate_timeline.py checks
    2. Data Preservation - Audit raw vs processed vs labs accuracy
    3. Episode Linking - Assess RelatedEpisode quality
    4. Categorization - Evaluate categorization accuracy
    5. Labs Integration - Check labs/exams integration quality
    6. Cross-Profile Consistency - Compare consistency across profiles
    7. Timeline Continuity - Assess long-running episode coherence

Outputs:
    - Phase-specific JSON and Markdown reports
    - Executive summary with quality scores
    - Per-profile detailed reports
    - Issue prioritization matrix
    - Remediation action plan
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Import phase scripts
try:
    from phase1_validation import run_phase1
    from phase2_preservation import run_phase2
    from phase3_linking import run_phase3
    from phase4_categorization import run_phase4
    from phase5_labs_integration import run_phase5
    from phase6_cross_profile import run_phase6
    from phase7_continuity import run_phase7
    from generate_reports import generate_all_reports
except ImportError as e:
    print(f"Error importing phase scripts: {e}")
    print("Make sure all phase scripts are in the same directory as this script")
    sys.exit(1)


def validate_paths(tiago_path, cristina_path):
    """Validate that output paths exist and contain required files."""
    errors = []

    for name, path in [("Tiago", tiago_path), ("Cristina", cristina_path)]:
        path = Path(path)
        if not path.exists():
            errors.append(f"{name} path does not exist: {path}")
            continue

        # Check for required files
        csv_file = path / "health_log.csv"
        if not csv_file.exists():
            errors.append(f"Missing health_log.csv in {name} path: {path}")

        entries_dir = path / "entries"
        if not entries_dir.exists():
            errors.append(f"Missing entries/ directory in {name} path: {path}")
        else:
            # Check for at least some processed files
            processed_files = list(entries_dir.glob("*.processed.md"))
            if not processed_files:
                errors.append(f"No processed.md files found in {name} entries/: {entries_dir}")

    return errors


def print_header(text):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80 + "\n")


def print_progress(phase_num, phase_name, status="Running"):
    """Print phase progress indicator."""
    print(f"\n[Phase {phase_num}/7] {phase_name}...")
    print(f"Status: {status}")


def main():
    parser = argparse.ArgumentParser(
        description="Run comprehensive quality review for parsehealthlog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic usage
    python run_quality_review.py \\
        --tiago-path ~/output/tiago \\
        --cristina-path ~/output/cristina \\
        --output-dir ./reports

    # With verbose output
    python run_quality_review.py \\
        --tiago-path ~/output/tiago \\
        --cristina-path ~/output/cristina \\
        --output-dir ./reports \\
        --verbose
        """
    )

    parser.add_argument(
        "--tiago-path",
        required=True,
        type=Path,
        help="Path to Tiago's output directory (contains health_log.csv and entries/)"
    )
    parser.add_argument(
        "--cristina-path",
        required=True,
        type=Path,
        help="Path to Cristina's output directory (contains health_log.csv and entries/)"
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for output reports"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--skip-phases",
        nargs="+",
        type=int,
        choices=range(1, 8),
        help="Skip specific phases (1-7)"
    )

    args = parser.parse_args()

    # Validate paths
    print_header("Health Log Parser Quality Review")
    print(f"Tiago output:    {args.tiago_path}")
    print(f"Cristina output: {args.cristina_path}")
    print(f"Reports output:  {args.output_dir}")

    print("\nValidating paths...")
    errors = validate_paths(args.tiago_path, args.cristina_path)
    if errors:
        print("\nValidation errors:")
        for error in errors:
            print(f"  ❌ {error}")
        sys.exit(1)
    print("✅ All paths validated")

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Track results from each phase
    all_results = {
        "metadata": {
            "run_date": datetime.now().isoformat(),
            "tiago_path": str(args.tiago_path),
            "cristina_path": str(args.cristina_path),
            "output_dir": str(args.output_dir)
        },
        "phases": {}
    }

    skip_phases = set(args.skip_phases or [])

    # Phase 1: Validation
    if 1 not in skip_phases:
        print_progress(1, "Validation Analysis")
        try:
            phase1_results = run_phase1(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase1"] = phase1_results
            print("✅ Phase 1 complete")
        except Exception as e:
            print(f"❌ Phase 1 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase1"] = {"error": str(e)}

    # Phase 2: Data Preservation
    if 2 not in skip_phases:
        print_progress(2, "Data Preservation Analysis")
        try:
            phase2_results = run_phase2(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase2"] = phase2_results
            print("✅ Phase 2 complete")
        except Exception as e:
            print(f"❌ Phase 2 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase2"] = {"error": str(e)}

    # Phase 3: Episode Linking
    if 3 not in skip_phases:
        print_progress(3, "Episode Linking Analysis")
        try:
            phase3_results = run_phase3(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase3"] = phase3_results
            print("✅ Phase 3 complete")
        except Exception as e:
            print(f"❌ Phase 3 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase3"] = {"error": str(e)}

    # Phase 4: Categorization
    if 4 not in skip_phases:
        print_progress(4, "Categorization Analysis")
        try:
            phase4_results = run_phase4(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase4"] = phase4_results
            print("✅ Phase 4 complete")
        except Exception as e:
            print(f"❌ Phase 4 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase4"] = {"error": str(e)}

    # Phase 5: Labs Integration
    if 5 not in skip_phases:
        print_progress(5, "Labs Integration Analysis")
        try:
            phase5_results = run_phase5(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase5"] = phase5_results
            print("✅ Phase 5 complete")
        except Exception as e:
            print(f"❌ Phase 5 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase5"] = {"error": str(e)}

    # Phase 6: Cross-Profile Consistency
    if 6 not in skip_phases:
        print_progress(6, "Cross-Profile Consistency Analysis")
        try:
            phase6_results = run_phase6(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase6"] = phase6_results
            print("✅ Phase 6 complete")
        except Exception as e:
            print(f"❌ Phase 6 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase6"] = {"error": str(e)}

    # Phase 7: Timeline Continuity
    if 7 not in skip_phases:
        print_progress(7, "Timeline Continuity Analysis")
        try:
            phase7_results = run_phase7(
                args.tiago_path,
                args.cristina_path,
                args.output_dir,
                verbose=args.verbose
            )
            all_results["phases"]["phase7"] = phase7_results
            print("✅ Phase 7 complete")
        except Exception as e:
            print(f"❌ Phase 7 failed: {e}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_results["phases"]["phase7"] = {"error": str(e)}

    # Generate comprehensive reports
    print_header("Generating Comprehensive Reports")
    try:
        reports = generate_all_reports(all_results, args.output_dir)
        all_results["reports"] = reports
        print("✅ All reports generated")
    except Exception as e:
        print(f"❌ Report generation failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        all_results["reports"] = {"error": str(e)}

    # Save complete results
    results_file = args.output_dir / "complete_results.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n✅ Complete results saved to: {results_file}")

    # Print summary
    print_header("Quality Review Summary")
    if "quality_scores" in all_results.get("reports", {}):
        scores = all_results["reports"]["quality_scores"]
        print(f"Tiago Quality Score:    {scores.get('tiago', 0):.1f}/100")
        print(f"Cristina Quality Score: {scores.get('cristina', 0):.1f}/100")

    if "issue_counts" in all_results.get("reports", {}):
        counts = all_results["reports"]["issue_counts"]
        print(f"\nTotal Issues: {counts.get('total', 0)}")
        print(f"  Critical: {counts.get('critical', 0)}")
        print(f"  High:     {counts.get('high', 0)}")
        print(f"  Medium:   {counts.get('medium', 0)}")
        print(f"  Low:      {counts.get('low', 0)}")

    print(f"\nReports generated in: {args.output_dir}")
    print("\nKey files:")
    print(f"  - executive_summary.md")
    print(f"  - tiago_quality_report.md")
    print(f"  - cristina_quality_report.md")
    print(f"  - issue_prioritization.md")
    print(f"  - remediation_plan.md")
    print(f"  - complete_results.json")

    print("\n" + "=" * 80)
    print("  Quality Review Complete!")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
