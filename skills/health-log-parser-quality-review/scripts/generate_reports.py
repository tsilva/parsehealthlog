#!/usr/bin/env python3
"""
Report Generation Module

Synthesizes all phase results into comprehensive reports:
- Executive summary with quality scores
- Per-profile detailed reports
- Issue prioritization matrix
- Remediation action plan
"""

import json
from pathlib import Path
from collections import defaultdict


def calculate_quality_score(all_results):
    """Calculate overall quality scores for each profile."""
    scores = {"tiago": 0.0, "cristina": 0.0}

    # Phase weights (total = 100)
    weights = {
        "phase1": 20,  # Validation
        "phase2": 20,  # Data Preservation
        "phase3": 15,  # Episode Linking
        "phase4": 15,  # Categorization
        "phase5": 10,  # Labs Integration
        "phase6": 10,  # Cross-Profile
        "phase7": 10,  # Timeline Continuity
    }

    for profile in ["tiago", "cristina"]:
        total_score = 0.0

        # Phase 1: Validation score
        if "phase1" in all_results["phases"]:
            phase1_score = all_results["phases"]["phase1"]["scores"].get(profile, 0)
            total_score += (phase1_score / 100) * weights["phase1"]

        # Phase 2: Data Preservation (inverse of issues)
        if "phase2" in all_results["phases"]:
            issues_count = all_results["phases"]["phase2"].get(f"{profile}_issues", 0)
            phase2_score = max(0, 100 - issues_count)
            total_score += (phase2_score / 100) * weights["phase2"]

        # Phase 3: Episode Linking
        if "phase3" in all_results["phases"]:
            link_completeness = all_results["phases"]["phase3"][profile]["stats"].get("link_completeness", 0)
            total_score += (link_completeness / 100) * weights["phase3"]

        # Phase 4: Categorization (inverse of issues)
        if "phase4" in all_results["phases"]:
            issues_count = all_results["phases"]["phase4"][profile].get("total_issues", 0)
            phase4_score = max(0, 100 - (issues_count * 2))  # 2 points per issue
            total_score += (phase4_score / 100) * weights["phase4"]

        # Phase 5: Labs Integration
        if "phase5" in all_results["phases"]:
            integration_rate = all_results["phases"]["phase5"][profile].get("integration_rate", 0)
            total_score += (integration_rate / 100) * weights["phase5"]

        # Phase 6: Cross-Profile (use consistency score)
        if "phase6" in all_results["phases"]:
            if profile == "tiago":
                consistency_score = all_results["phases"]["phase6"]["voice_consistency"].get("consistency_score", 0)
                total_score += (consistency_score / 100) * weights["phase6"]
            else:
                consistency_score = all_results["phases"]["phase6"]["voice_consistency"].get("consistency_score", 0)
                total_score += (consistency_score / 100) * weights["phase6"]

        # Phase 7: Timeline Continuity
        if "phase7" in all_results["phases"]:
            total_analyzed = all_results["phases"]["phase7"][profile].get("total_analyzed", 0)
            coherent_count = all_results["phases"]["phase7"][profile].get("coherent_count", 0)
            phase7_score = (coherent_count / total_analyzed * 100) if total_analyzed > 0 else 0
            total_score += (phase7_score / 100) * weights["phase7"]

        scores[profile] = total_score

    return scores


def collect_all_issues(all_results):
    """Collect and categorize all issues from all phases."""
    all_issues = []

    # Phase 1: Validation
    if "phase1" in all_results["phases"]:
        for profile in ["tiago", "cristina"]:
            for category, issues in all_results["phases"]["phase1"][profile]["by_category"].items():
                for issue in issues:
                    all_issues.append({
                        "phase": "Phase 1: Validation",
                        "profile": profile.title(),
                        "severity": "high" if category in ["episode_continuity", "related_episodes"] else "medium",
                        "category": category,
                        "description": issue.get("message", "")
                    })

    # Phase 2: Data Preservation
    if "phase2" in all_results["phases"]:
        for issue in all_results["phases"]["phase2"].get("issues", []):
            all_issues.append({
                "phase": "Phase 2: Data Preservation",
                "profile": issue["profile"],
                "severity": issue["severity"],
                "category": issue["issue_type"],
                "description": issue["description"]
            })

    # Phase 3: Episode Linking
    if "phase3" in all_results["phases"]:
        for profile in ["tiago", "cristina"]:
            for issue in all_results["phases"]["phase3"][profile].get("issues", []):
                all_issues.append({
                    "phase": "Phase 3: Episode Linking",
                    "profile": profile.title(),
                    "severity": issue["severity"],
                    "category": issue["issue_type"],
                    "description": issue["description"]
                })

    # Phase 4: Categorization
    if "phase4" in all_results["phases"]:
        for profile in ["tiago", "cristina"]:
            for issue in all_results["phases"]["phase4"][profile].get("issues", []):
                all_issues.append({
                    "phase": "Phase 4: Categorization",
                    "profile": profile.title(),
                    "severity": issue["severity"],
                    "category": issue["issue_type"],
                    "description": issue["description"]
                })

    return all_issues


def generate_executive_summary(all_results, quality_scores, all_issues, output_dir):
    """Generate executive summary report."""
    report = []
    report.append("# Health Log Parser Quality Review - Executive Summary\n")
    report.append(f"**Review Date:** {all_results['metadata']['run_date']}\n\n")

    # Quality Scores
    report.append("## Overall Quality Scores\n")
    report.append(f"- **Tiago:** {quality_scores['tiago']:.1f}/100\n")
    report.append(f"- **Cristina:** {quality_scores['cristina']:.1f}/100\n\n")

    # Interpretation
    avg_score = (quality_scores['tiago'] + quality_scores['cristina']) / 2
    if avg_score >= 80:
        status = "✅ Excellent"
        assessment = "Both profiles demonstrate high extraction quality with minor issues."
    elif avg_score >= 70:
        status = "⚠️ Good"
        assessment = "Both profiles show good quality with some areas for improvement."
    elif avg_score >= 60:
        status = "⚠️ Fair"
        assessment = "Moderate quality issues detected that should be addressed."
    else:
        status = "❌ Needs Improvement"
        assessment = "Significant quality issues require attention."

    report.append(f"**Status:** {status}\n")
    report.append(f"**Assessment:** {assessment}\n\n")

    # Issue Summary
    severity_counts = defaultdict(int)
    for issue in all_issues:
        severity_counts[issue["severity"]] += 1

    report.append("## Issue Summary\n")
    report.append(f"**Total Issues:** {len(all_issues)}\n")
    report.append(f"- Critical: {severity_counts['critical']}\n")
    report.append(f"- High: {severity_counts['high']}\n")
    report.append(f"- Medium: {severity_counts['medium']}\n")
    report.append(f"- Low: {severity_counts['low']}\n\n")

    # Phase Highlights
    report.append("## Phase Highlights\n")

    if "phase1" in all_results["phases"]:
        report.append("### Phase 1: Validation\n")
        report.append(f"- Tiago: {all_results['phases']['phase1']['scores']['tiago']:.1f}/100\n")
        report.append(f"- Cristina: {all_results['phases']['phase1']['scores']['cristina']:.1f}/100\n\n")

    if "phase3" in all_results["phases"]:
        report.append("### Phase 3: Episode Linking\n")
        t_rate = all_results["phases"]["phase3"]["tiago"]["stats"]["link_completeness"]
        c_rate = all_results["phases"]["phase3"]["cristina"]["stats"]["link_completeness"]
        report.append(f"- Tiago Link Completeness: {t_rate:.1f}%\n")
        report.append(f"- Cristina Link Completeness: {c_rate:.1f}%\n\n")

    if "phase5" in all_results["phases"]:
        report.append("### Phase 5: Labs Integration\n")
        t_rate = all_results["phases"]["phase5"]["tiago"]["integration_rate"]
        c_rate = all_results["phases"]["phase5"]["cristina"]["integration_rate"]
        report.append(f"- Tiago Integration Rate: {t_rate:.1f}%\n")
        report.append(f"- Cristina Integration Rate: {c_rate:.1f}%\n\n")

    # Recommendations
    report.append("## Key Recommendations\n")
    if severity_counts["critical"] > 0:
        report.append(f"1. **Address {severity_counts['critical']} critical issues immediately** - Focus on data preservation and validation errors\n")
    if quality_scores["tiago"] < 70 or quality_scores["cristina"] < 70:
        report.append("2. **Review and update processing prompts** - Multiple phases show systematic issues\n")
    if "phase3" in all_results["phases"]:
        t_rate = all_results["phases"]["phase3"]["tiago"]["stats"]["link_completeness"]
        c_rate = all_results["phases"]["phase3"]["cristina"]["stats"]["link_completeness"]
        if t_rate < 70 or c_rate < 70:
            report.append("3. **Improve episode linking logic** - Many treatments lack condition links\n")

    report.append("\n## Next Steps\n")
    report.append("1. Review detailed profile reports (tiago_quality_report.md, cristina_quality_report.md)\n")
    report.append("2. Prioritize issues using issue_prioritization.md\n")
    report.append("3. Implement fixes according to remediation_plan.md\n")
    report.append("4. Re-run quality review after fixes to measure improvement\n")

    report_path = output_dir / "executive_summary.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return str(report_path)


def generate_profile_report(profile_name, all_results, quality_score, all_issues, output_dir):
    """Generate detailed profile-specific report."""
    profile_issues = [i for i in all_issues if i["profile"].lower() == profile_name.lower()]

    report = []
    report.append(f"# {profile_name} Profile - Quality Report\n\n")
    report.append(f"**Overall Quality Score:** {quality_score:.1f}/100\n\n")

    report.append("## Phase Breakdown\n")

    # Add phase-specific details
    phases_info = {
        "phase1": "Validation",
        "phase2": "Data Preservation",
        "phase3": "Episode Linking",
        "phase4": "Categorization",
        "phase5": "Labs Integration",
        "phase7": "Timeline Continuity"
    }

    for phase_key, phase_name in phases_info.items():
        if phase_key in all_results["phases"]:
            report.append(f"### {phase_name}\n")

            if phase_key == "phase1":
                score = all_results["phases"]["phase1"]["scores"].get(profile_name.lower(), 0)
                report.append(f"**Score:** {score:.1f}/100\n\n")

            elif phase_key == "phase2":
                issues_count = len([i for i in all_results["phases"]["phase2"].get("issues", [])
                                   if i["profile"] == profile_name])
                report.append(f"**Issues Found:** {issues_count}\n\n")

            elif phase_key == "phase3":
                stats = all_results["phases"]["phase3"][profile_name.lower()]["stats"]
                report.append(f"**Link Completeness:** {stats['link_completeness']:.1f}%\n")
                report.append(f"**Orphaned References:** {stats['orphaned_references']}\n\n")

            elif phase_key == "phase4":
                issues_count = all_results["phases"]["phase4"][profile_name.lower()]["total_issues"]
                report.append(f"**Categorization Issues:** {issues_count}\n\n")

            elif phase_key == "phase5":
                rate = all_results["phases"]["phase5"][profile_name.lower()]["integration_rate"]
                report.append(f"**Integration Rate:** {rate:.1f}%\n\n")

            elif phase_key == "phase7":
                total = all_results["phases"]["phase7"][profile_name.lower()]["total_analyzed"]
                coherent = all_results["phases"]["phase7"][profile_name.lower()]["coherent_count"]
                report.append(f"**Coherent Episodes:** {coherent}/{total}\n\n")

    # Issues by severity
    report.append("## Issues by Severity\n")
    by_severity = defaultdict(list)
    for issue in profile_issues:
        by_severity[issue["severity"]].append(issue)

    for severity in ["critical", "high", "medium", "low"]:
        if severity in by_severity:
            report.append(f"### {severity.title()} ({len(by_severity[severity])})\n")
            for issue in by_severity[severity][:5]:
                report.append(f"- **{issue['phase']}**: {issue['description']}\n")
            if len(by_severity[severity]) > 5:
                report.append(f"- _(and {len(by_severity[severity]) - 5} more)_\n")
            report.append("\n")

    report_path = output_dir / f"{profile_name.lower()}_quality_report.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return str(report_path)


def generate_issue_prioritization(all_issues, output_dir):
    """Generate issue prioritization matrix."""
    report = []
    report.append("# Issue Prioritization Matrix\n\n")

    # Priority scoring: critical=100, high=50, medium=25, low=10
    priority_scores = {"critical": 100, "high": 50, "medium": 25, "low": 10}

    # Group by category and calculate priority
    by_category = defaultdict(list)
    for issue in all_issues:
        by_category[issue["category"]].append(issue)

    category_priorities = []
    for category, issues in by_category.items():
        total_priority = sum(priority_scores.get(i["severity"], 0) for i in issues)
        category_priorities.append({
            "category": category,
            "count": len(issues),
            "priority_score": total_priority,
            "issues": issues
        })

    # Sort by priority score
    category_priorities.sort(key=lambda x: x["priority_score"], reverse=True)

    report.append("## Priority Ranking\n")
    for i, cat_info in enumerate(category_priorities, 1):
        report.append(f"{i}. **{cat_info['category'].replace('_', ' ').title()}** ")
        report.append(f"(Priority Score: {cat_info['priority_score']}, Count: {cat_info['count']})\n")

    report.append("\n## Detailed Breakdown\n")
    for cat_info in category_priorities[:10]:  # Top 10 categories
        report.append(f"\n### {cat_info['category'].replace('_', ' ').title()}\n")
        report.append(f"**Count:** {cat_info['count']}\n")
        report.append(f"**Priority Score:** {cat_info['priority_score']}\n")

        severity_counts = defaultdict(int)
        for issue in cat_info['issues']:
            severity_counts[issue['severity']] += 1

        report.append(f"**Severity Breakdown:** Critical: {severity_counts['critical']}, ")
        report.append(f"High: {severity_counts['high']}, ")
        report.append(f"Medium: {severity_counts['medium']}, ")
        report.append(f"Low: {severity_counts['low']}\n\n")

    report_path = output_dir / "issue_prioritization.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return str(report_path)


def generate_remediation_plan(all_issues, output_dir):
    """Generate remediation action plan."""
    report = []
    report.append("# Remediation Action Plan\n\n")

    report.append("## Immediate Actions (Critical Issues)\n")
    critical_issues = [i for i in all_issues if i["severity"] == "critical"]
    if critical_issues:
        by_category = defaultdict(list)
        for issue in critical_issues:
            by_category[issue["category"]].append(issue)

        for category, issues in by_category.items():
            report.append(f"### {category.replace('_', ' ').title()} ({len(issues)} issues)\n")
            report.append("**Action:** Review and fix processing logic\n")
            report.append(f"**Impact:** {len(issues)} entries affected\n\n")
    else:
        report.append("✅ No critical issues found\n\n")

    report.append("## Short-term Actions (High Priority)\n")
    high_issues = [i for i in all_issues if i["severity"] == "high"]
    if high_issues:
        by_category = defaultdict(list)
        for issue in high_issues:
            by_category[issue["category"]].append(issue)

        for category, issues in list(by_category.items())[:5]:  # Top 5
            report.append(f"### {category.replace('_', ' ').title()} ({len(issues)} issues)\n")
            report.append("**Recommended Actions:**\n")
            if "dosage" in category:
                report.append("- Review medication extraction patterns in prompts\n")
                report.append("- Add dosage validation checks\n")
            elif "link" in category:
                report.append("- Improve episode linking logic in update_timeline prompt\n")
                report.append("- Add post-processing validation for orphaned references\n")
            else:
                report.append("- Review processing prompt for this category\n")
                report.append("- Add specific validation rules\n")
            report.append("\n")

    report.append("## Medium-term Actions\n")
    report.append("1. Review and update all processing prompts based on findings\n")
    report.append("2. Implement additional validation checks in main.py\n")
    report.append("3. Re-run processing with --force-reprocess\n")
    report.append("4. Re-run quality review to verify improvements\n\n")

    report.append("## Long-term Improvements\n")
    report.append("1. Establish regular quality review schedule (e.g., quarterly)\n")
    report.append("2. Add automated quality checks to CI/CD pipeline\n")
    report.append("3. Monitor quality scores over time\n")
    report.append("4. Continuously refine prompts based on quality metrics\n")

    report_path = output_dir / "remediation_plan.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return str(report_path)


def generate_all_reports(all_results, output_dir):
    """Generate all comprehensive reports."""
    output_dir = Path(output_dir)

    # Calculate quality scores
    quality_scores = calculate_quality_score(all_results)

    # Collect all issues
    all_issues = collect_all_issues(all_results)

    # Generate reports
    reports = {}

    reports["executive_summary"] = generate_executive_summary(
        all_results, quality_scores, all_issues, output_dir
    )

    reports["tiago_report"] = generate_profile_report(
        "Tiago", all_results, quality_scores["tiago"], all_issues, output_dir
    )

    reports["cristina_report"] = generate_profile_report(
        "Cristina", all_results, quality_scores["cristina"], all_issues, output_dir
    )

    reports["issue_prioritization"] = generate_issue_prioritization(
        all_issues, output_dir
    )

    reports["remediation_plan"] = generate_remediation_plan(
        all_issues, output_dir
    )

    # Add metadata
    reports["quality_scores"] = quality_scores
    reports["issue_counts"] = {
        "total": len(all_issues),
        "critical": len([i for i in all_issues if i["severity"] == "critical"]),
        "high": len([i for i in all_issues if i["severity"] == "high"]),
        "medium": len([i for i in all_issues if i["severity"] == "medium"]),
        "low": len([i for i in all_issues if i["severity"] == "low"])
    }

    return reports


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python generate_reports.py <complete_results.json>")
        sys.exit(1)

    results_file = Path(sys.argv[1])
    with open(results_file) as f:
        all_results = json.load(f)

    output_dir = results_file.parent
    reports = generate_all_reports(all_results, output_dir)

    print("Reports generated:")
    for report_name, report_path in reports.items():
        if isinstance(report_path, str):
            print(f"  - {report_path}")
