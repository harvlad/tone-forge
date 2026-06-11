"""HTML report generator for MIDI extraction benchmarks.

Generates comprehensive HTML reports with:
- Embedded visualizations
- Metrics summary tables
- Worst sample highlights
- Regression tracking
"""
from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ReportConfig:
    """Configuration for report generation."""

    # Output settings
    embed_images: bool = True  # Embed as base64 vs external files
    image_format: str = "png"
    image_dpi: int = 150

    # Content settings
    include_heatmaps: bool = True
    include_pitch_analysis: bool = True
    include_timing_analysis: bool = True
    include_piano_roll: bool = True
    include_worst_samples: bool = True
    max_worst_samples: int = 10

    # Piano roll settings
    piano_roll_window_size: float = 10.0  # Seconds per view

    # Styling
    css_theme: str = "default"  # "default", "dark", "light"


@dataclass
class SampleReport:
    """Report data for a single sample."""

    sample_id: str
    f1: float
    precision: float
    recall: float

    # Note counts
    extracted_count: int = 0
    ground_truth_count: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    # Metadata
    stem_type: str = ""
    genre: str = ""
    profile_used: str = ""

    # Embedded images (base64)
    heatmap_image: Optional[str] = None
    piano_roll_image: Optional[str] = None
    pitch_confusion_image: Optional[str] = None
    timing_histogram_image: Optional[str] = None


@dataclass
class BenchmarkReport:
    """Complete benchmark report data."""

    # Identification
    manifest_name: str
    run_timestamp: str
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None

    # Overall metrics
    overall_f1: float = 0.0
    overall_precision: float = 0.0
    overall_recall: float = 0.0
    total_samples: int = 0
    successful_samples: int = 0

    # Breakdowns
    per_stem_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    per_genre_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Sample reports
    sample_reports: List[SampleReport] = field(default_factory=list)
    worst_samples: List[SampleReport] = field(default_factory=list)

    # Regression info
    baseline_comparison: Optional[Dict[str, float]] = None
    has_regressions: bool = False
    regression_alerts: List[Dict[str, Any]] = field(default_factory=list)

    # Aggregate visualizations (base64)
    aggregate_heatmap: Optional[str] = None
    aggregate_pitch_confusion: Optional[str] = None
    aggregate_timing_histogram: Optional[str] = None


def _fig_to_base64(fig: Any, format: str = "png", dpi: int = 150) -> str:
    """Convert matplotlib figure to base64 string."""
    try:
        import matplotlib.pyplot as plt

        buf = io.BytesIO()
        fig.savefig(buf, format=format, dpi=dpi, bbox_inches='tight')
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        return f"data:image/{format};base64,{img_str}"
    except Exception as e:
        logger.warning(f"Failed to convert figure to base64: {e}")
        return ""


def _get_css_styles(theme: str = "default") -> str:
    """Get CSS styles for report."""
    if theme == "dark":
        bg_color = "#1e1e1e"
        text_color = "#e0e0e0"
        card_bg = "#2d2d2d"
        border_color = "#444"
        accent = "#4a9eff"
    elif theme == "light":
        bg_color = "#ffffff"
        text_color = "#333333"
        card_bg = "#f5f5f5"
        border_color = "#ddd"
        accent = "#0066cc"
    else:  # default
        bg_color = "#f8f9fa"
        text_color = "#212529"
        card_bg = "#ffffff"
        border_color = "#dee2e6"
        accent = "#0d6efd"

    return f"""
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background-color: {bg_color};
        color: {text_color};
        margin: 0;
        padding: 20px;
        line-height: 1.6;
    }}
    .container {{
        max-width: 1400px;
        margin: 0 auto;
    }}
    h1, h2, h3 {{
        color: {text_color};
        border-bottom: 2px solid {accent};
        padding-bottom: 8px;
    }}
    .card {{
        background: {card_bg};
        border: 1px solid {border_color};
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }}
    .metrics-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 15px;
    }}
    .metric-box {{
        background: {card_bg};
        border: 1px solid {border_color};
        border-radius: 6px;
        padding: 15px;
        text-align: center;
    }}
    .metric-value {{
        font-size: 2em;
        font-weight: bold;
        color: {accent};
    }}
    .metric-label {{
        font-size: 0.9em;
        color: #666;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 15px 0;
    }}
    th, td {{
        padding: 10px;
        border: 1px solid {border_color};
        text-align: left;
    }}
    th {{
        background: {accent};
        color: white;
    }}
    tr:nth-child(even) {{
        background: {card_bg};
    }}
    .good {{ color: #28a745; }}
    .warning {{ color: #ffc107; }}
    .bad {{ color: #dc3545; }}
    .image-container {{
        text-align: center;
        margin: 20px 0;
    }}
    .image-container img {{
        max-width: 100%;
        height: auto;
        border: 1px solid {border_color};
        border-radius: 4px;
    }}
    .regression-alert {{
        background: #fff3cd;
        border: 1px solid #ffc107;
        border-radius: 4px;
        padding: 15px;
        margin: 10px 0;
    }}
    .regression-critical {{
        background: #f8d7da;
        border-color: #dc3545;
    }}
    .sample-card {{
        border-left: 4px solid {accent};
    }}
    .collapsible {{
        cursor: pointer;
        padding: 10px;
        background: {card_bg};
        border: none;
        width: 100%;
        text-align: left;
        font-size: 1.1em;
    }}
    .collapsible:after {{
        content: '\\002B';
        float: right;
    }}
    .collapsible.active:after {{
        content: '\\2212';
    }}
    .collapse-content {{
        display: none;
        padding: 10px;
        overflow: hidden;
    }}
    """


def _get_html_template() -> str:
    """Get HTML template."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MIDI Benchmark Report - {manifest_name}</title>
    <style>
    {css_styles}
    </style>
</head>
<body>
    <div class="container">
        <h1>MIDI Extraction Benchmark Report</h1>

        <div class="card">
            <h2>Summary</h2>
            <p><strong>Manifest:</strong> {manifest_name}</p>
            <p><strong>Run Date:</strong> {run_timestamp}</p>
            {git_info}

            <div class="metrics-grid">
                <div class="metric-box">
                    <div class="metric-value {f1_class}">{overall_f1:.1%}</div>
                    <div class="metric-label">F1 Score</div>
                </div>
                <div class="metric-box">
                    <div class="metric-value">{overall_precision:.1%}</div>
                    <div class="metric-label">Precision</div>
                </div>
                <div class="metric-box">
                    <div class="metric-value">{overall_recall:.1%}</div>
                    <div class="metric-label">Recall</div>
                </div>
                <div class="metric-box">
                    <div class="metric-value">{successful_samples}/{total_samples}</div>
                    <div class="metric-label">Samples</div>
                </div>
            </div>
        </div>

        {regression_section}

        {breakdown_section}

        {visualization_section}

        {worst_samples_section}

        {sample_details_section}

        <footer style="text-align: center; margin-top: 40px; color: #666;">
            <p>Generated by ToneForge MIDI Benchmark Suite</p>
        </footer>
    </div>

    <script>
    document.querySelectorAll('.collapsible').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
            this.classList.toggle('active');
            var content = this.nextElementSibling;
            if (content.style.display === 'block') {{
                content.style.display = 'none';
            }} else {{
                content.style.display = 'block';
            }}
        }});
    }});
    </script>
</body>
</html>
"""


def generate_html_report(
    report: BenchmarkReport,
    output_path: Path,
    config: Optional[ReportConfig] = None,
) -> Path:
    """Generate HTML report from benchmark data.

    Args:
        report: BenchmarkReport with all data
        output_path: Path to save HTML file
        config: Report configuration

    Returns:
        Path to generated report
    """
    config = config or ReportConfig()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get CSS
    css_styles = _get_css_styles(config.css_theme)

    # F1 color class
    if report.overall_f1 >= 0.8:
        f1_class = "good"
    elif report.overall_f1 >= 0.6:
        f1_class = "warning"
    else:
        f1_class = "bad"

    # Git info
    git_info = ""
    if report.git_commit:
        git_info = f'<p><strong>Git:</strong> {report.git_branch or "unknown"} @ {report.git_commit[:8]}</p>'

    # Regression section
    regression_section = ""
    if report.has_regressions and report.regression_alerts:
        alerts_html = ""
        for alert in report.regression_alerts:
            severity_class = "regression-critical" if alert.get("severity") == "critical" else ""
            alerts_html += f"""
            <div class="regression-alert {severity_class}">
                <strong>{alert.get('metric_name', 'Unknown')}</strong>:
                {alert.get('baseline_value', 0):.1%} → {alert.get('current_value', 0):.1%}
                ({alert.get('delta_percent', 0):+.1%})
            </div>
            """
        regression_section = f"""
        <div class="card">
            <h2>Regression Alerts</h2>
            {alerts_html}
        </div>
        """
    elif report.baseline_comparison:
        comparison_html = "<table><tr><th>Metric</th><th>Delta</th></tr>"
        for metric, delta in report.baseline_comparison.items():
            color = "good" if delta >= 0 else "bad"
            comparison_html += f'<tr><td>{metric}</td><td class="{color}">{delta:+.1%}</td></tr>'
        comparison_html += "</table>"
        regression_section = f"""
        <div class="card">
            <h2>Comparison to Baseline</h2>
            {comparison_html}
        </div>
        """

    # Breakdown section
    breakdown_section = ""

    if report.per_stem_metrics:
        stem_table = "<table><tr><th>Stem</th><th>F1</th><th>Precision</th><th>Recall</th><th>Count</th></tr>"
        for stem, metrics in report.per_stem_metrics.items():
            stem_table += f"""
            <tr>
                <td>{stem}</td>
                <td>{metrics.get('f1', 0):.1%}</td>
                <td>{metrics.get('precision', 0):.1%}</td>
                <td>{metrics.get('recall', 0):.1%}</td>
                <td>{metrics.get('count', 0)}</td>
            </tr>
            """
        stem_table += "</table>"
        breakdown_section += f"""
        <div class="card">
            <h2>Per-Stem Breakdown</h2>
            {stem_table}
        </div>
        """

    if report.per_genre_metrics:
        genre_table = "<table><tr><th>Genre</th><th>F1</th><th>Precision</th><th>Recall</th><th>Count</th></tr>"
        for genre, metrics in report.per_genre_metrics.items():
            genre_table += f"""
            <tr>
                <td>{genre}</td>
                <td>{metrics.get('f1', 0):.1%}</td>
                <td>{metrics.get('precision', 0):.1%}</td>
                <td>{metrics.get('recall', 0):.1%}</td>
                <td>{metrics.get('count', 0)}</td>
            </tr>
            """
        genre_table += "</table>"
        breakdown_section += f"""
        <div class="card">
            <h2>Per-Genre Breakdown</h2>
            {genre_table}
        </div>
        """

    # Visualization section
    visualization_section = ""

    if report.aggregate_heatmap:
        visualization_section += f"""
        <div class="card">
            <h2>Error Heatmap</h2>
            <div class="image-container">
                <img src="{report.aggregate_heatmap}" alt="Error Heatmap">
            </div>
        </div>
        """

    if report.aggregate_pitch_confusion:
        visualization_section += f"""
        <div class="card">
            <h2>Pitch Confusion Analysis</h2>
            <div class="image-container">
                <img src="{report.aggregate_pitch_confusion}" alt="Pitch Confusion">
            </div>
        </div>
        """

    if report.aggregate_timing_histogram:
        visualization_section += f"""
        <div class="card">
            <h2>Timing Error Distribution</h2>
            <div class="image-container">
                <img src="{report.aggregate_timing_histogram}" alt="Timing Histogram">
            </div>
        </div>
        """

    # Worst samples section
    worst_samples_section = ""
    if report.worst_samples:
        samples_html = ""
        for sample in report.worst_samples[:config.max_worst_samples]:
            images_html = ""
            if sample.piano_roll_image:
                images_html += f'<img src="{sample.piano_roll_image}" alt="Piano Roll" style="max-width: 100%;">'

            samples_html += f"""
            <div class="card sample-card">
                <h3>{sample.sample_id}</h3>
                <div class="metrics-grid">
                    <div class="metric-box">
                        <div class="metric-value bad">{sample.f1:.1%}</div>
                        <div class="metric-label">F1</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value">{sample.precision:.1%}</div>
                        <div class="metric-label">Precision</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-value">{sample.recall:.1%}</div>
                        <div class="metric-label">Recall</div>
                    </div>
                </div>
                <p><strong>Notes:</strong> {sample.extracted_count} extracted, {sample.ground_truth_count} ground truth</p>
                <p><strong>TP:</strong> {sample.true_positives}, <strong>FP:</strong> {sample.false_positives}, <strong>FN:</strong> {sample.false_negatives}</p>
                {images_html}
            </div>
            """

        worst_samples_section = f"""
        <div class="card">
            <h2>Worst Performing Samples</h2>
            {samples_html}
        </div>
        """

    # Sample details section (collapsible)
    sample_details_section = ""
    if report.sample_reports:
        samples_list = ""
        for sample in sorted(report.sample_reports, key=lambda s: -s.f1):
            f1_class = "good" if sample.f1 >= 0.8 else "warning" if sample.f1 >= 0.6 else "bad"
            samples_list += f"""
            <tr>
                <td>{sample.sample_id}</td>
                <td class="{f1_class}">{sample.f1:.1%}</td>
                <td>{sample.precision:.1%}</td>
                <td>{sample.recall:.1%}</td>
                <td>{sample.stem_type}</td>
                <td>{sample.genre}</td>
            </tr>
            """

        sample_details_section = f"""
        <div class="card">
            <button class="collapsible">All Sample Results ({len(report.sample_reports)} samples)</button>
            <div class="collapse-content">
                <table>
                    <tr><th>Sample</th><th>F1</th><th>Precision</th><th>Recall</th><th>Stem</th><th>Genre</th></tr>
                    {samples_list}
                </table>
            </div>
        </div>
        """

    # Generate HTML
    html = _get_html_template().format(
        manifest_name=report.manifest_name,
        run_timestamp=report.run_timestamp,
        git_info=git_info,
        css_styles=css_styles,
        f1_class=f1_class,
        overall_f1=report.overall_f1,
        overall_precision=report.overall_precision,
        overall_recall=report.overall_recall,
        successful_samples=report.successful_samples,
        total_samples=report.total_samples,
        regression_section=regression_section,
        breakdown_section=breakdown_section,
        visualization_section=visualization_section,
        worst_samples_section=worst_samples_section,
        sample_details_section=sample_details_section,
    )

    # Write file
    with open(output_path, "w") as f:
        f.write(html)

    logger.info(f"Generated HTML report at {output_path}")
    return output_path


def create_report_from_benchmark(
    benchmark_result: Any,
    output_path: Path,
    config: Optional[ReportConfig] = None,
    generate_visualizations: bool = True,
) -> BenchmarkReport:
    """Create report from benchmark result with optional visualization generation.

    Args:
        benchmark_result: BenchmarkRunResult from parallel runner
        output_path: Path to save report
        config: Report configuration
        generate_visualizations: Whether to generate visualization images

    Returns:
        BenchmarkReport
    """
    config = config or ReportConfig()

    # Build report
    report = BenchmarkReport(
        manifest_name=benchmark_result.manifest_name,
        run_timestamp=benchmark_result.run_timestamp,
        git_commit=benchmark_result.git_commit,
        git_branch=benchmark_result.git_branch,
        overall_f1=benchmark_result.aggregate_metrics.overall_f1,
        overall_precision=benchmark_result.aggregate_metrics.overall_precision,
        overall_recall=benchmark_result.aggregate_metrics.overall_recall,
        total_samples=benchmark_result.aggregate_metrics.total_samples,
        successful_samples=benchmark_result.aggregate_metrics.successful_samples,
        baseline_comparison=benchmark_result.baseline_comparison,
    )

    # Per-stem metrics
    for stem, f1 in benchmark_result.aggregate_metrics.per_stem_f1.items():
        report.per_stem_metrics[stem] = {
            "f1": f1,
            "precision": benchmark_result.aggregate_metrics.per_stem_precision.get(stem, 0),
            "recall": benchmark_result.aggregate_metrics.per_stem_recall.get(stem, 0),
            "count": benchmark_result.aggregate_metrics.per_stem_count.get(stem, 0),
        }

    # Per-genre metrics
    for genre, f1 in benchmark_result.aggregate_metrics.per_genre_f1.items():
        report.per_genre_metrics[genre] = {
            "f1": f1,
            "precision": benchmark_result.aggregate_metrics.per_genre_precision.get(genre, 0),
            "recall": benchmark_result.aggregate_metrics.per_genre_recall.get(genre, 0),
            "count": benchmark_result.aggregate_metrics.per_genre_count.get(genre, 0),
        }

    # Sample reports
    for result in benchmark_result.sample_results:
        if result.success:
            sample_report = SampleReport(
                sample_id=result.sample_id,
                f1=result.f1,
                precision=result.precision,
                recall=result.recall,
                extracted_count=result.extracted_note_count,
                ground_truth_count=result.ground_truth_note_count,
                true_positives=result.true_positives,
                false_positives=result.false_positives,
                false_negatives=result.false_negatives,
                stem_type=result.stem_type,
                genre=result.genre,
                profile_used=result.profile_used or "",
            )
            report.sample_reports.append(sample_report)

    # Worst samples
    sorted_samples = sorted(report.sample_reports, key=lambda s: s.f1)
    report.worst_samples = sorted_samples[:config.max_worst_samples]

    # Generate HTML
    generate_html_report(report, output_path, config)

    return report
