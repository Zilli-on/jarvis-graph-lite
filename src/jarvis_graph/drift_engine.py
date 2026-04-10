"""drift_engine: compute the delta between two health-report snapshots.

A "snapshot" is the structured `summary` payload that `health_report` returns
(and that the CLI writes via `--json`). Loading two of them and asking
"what got worse?" is the natural use of the report once you've taken it once:
the report alone tells you the *state*, drift tells you the *direction*.

Drift has two flavours:

1. **Scalar drift** — numeric counters (`hotspot_count`, `dead_code_count`,
   `cycles.count`, etc.) compared baseline-vs-current with a sign and a
   direction label. Each metric is tagged `improved`, `worsened`, `unchanged`,
   or `neutral` based on whether the metric is one we want to push down
   (most of them) or just an informational total (file/symbol counts).

2. **Set drift** — ranked lists (top hotspots, top god files, dead-code
   symbols, ...) compared as sets keyed by the entry's stable id
   (`qualified_name` for symbols, `rel_path` for files). The result is a
   list of `regressions` (newly in the bad list) and `improvements` (left
   the bad list), plus the count of entries that stayed put.

The engine is pure: it does not touch the database, it only consumes the
JSON-serialisable shape produced by `health_report_engine.HealthReport.summary`.
That keeps it deterministic, trivially testable, and usable by external
agents that just want to feed in two JSON files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# How each scalar metric should be interpreted. Direction is "down" when a
# lower number is better (the usual case for any health-check counter),
# "up" when higher is better (resolution percentages), or "neutral" when
# the metric is purely informational.
_SCALAR_SPECS: list[tuple[str, str, str]] = [
    # (label, dotted-path, direction)
    ("files",                          "headline.files",                       "neutral"),
    ("symbols",                        "headline.symbols",                     "neutral"),
    ("parse errors",                   "headline.files_with_parse_errors",     "down"),
    ("import resolution %",            "headline.imports_resolution_pct",      "up"),
    ("call resolution %",              "headline.calls_resolution_pct",        "up"),
    ("avg cyclomatic",                 "complexity.average",                   "down"),
    ("high-complexity callables",      "complexity.high",                      "down"),
    ("extreme-complexity callables",   "complexity.extreme",                   "down"),
    ("complexity hotspots (top-N)",    "complexity.hotspot_count",             "down"),
    ("functions over line threshold",  "long_functions.over_threshold",        "down"),
    ("dead code candidates",           "dead_code.count",                      "down"),
    ("unused imports",                 "unused_imports.count",                 "down"),
    ("import cycles",                  "cycles.count",                         "down"),
    ("high fan-out files",             "fan_out.count",                        "down"),
    ("coverage %",                     "coverage.coverage_pct",                "up"),
    ("coverage gaps (top-N)",          "coverage.gap_count",                   "down"),
]


@dataclass
class ScalarDrift:
    name: str
    baseline: float
    current: float
    delta: float
    direction: str          # "improved", "worsened", "unchanged", "neutral"


@dataclass
class SetDrift:
    name: str
    regressions: list[str]  # appears in current, not in baseline
    improvements: list[str]  # appears in baseline, not in current
    unchanged: int
    baseline_size: int
    current_size: int


@dataclass
class DriftReport:
    has_baseline: bool
    scalars: list[ScalarDrift] = field(default_factory=list)
    sets: list[SetDrift] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def regression_count(self) -> int:
        n = sum(1 for s in self.scalars if s.direction == "worsened")
        n += sum(len(s.regressions) for s in self.sets)
        return n

    @property
    def improvement_count(self) -> int:
        n = sum(1 for s in self.scalars if s.direction == "improved")
        n += sum(len(s.improvements) for s in self.sets)
        return n


def _walk(payload: dict, dotted: str) -> Any:
    """Resolve a dotted path inside a nested dict, returning None if any
    segment is missing. Tolerant by design: snapshots from older versions
    of the tool may not have every field."""
    cur: Any = payload
    for seg in dotted.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _direction(spec: str, baseline: float, current: float) -> str:
    if baseline == current:
        return "unchanged"
    if spec == "neutral":
        return "neutral"
    if spec == "down":
        return "improved" if current < baseline else "worsened"
    if spec == "up":
        return "improved" if current > baseline else "worsened"
    return "neutral"


def _coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _scalar_drifts(baseline: dict, current: dict) -> list[ScalarDrift]:
    out: list[ScalarDrift] = []
    for label, path, spec in _SCALAR_SPECS:
        b = _coerce_number(_walk(baseline, path))
        c = _coerce_number(_walk(current, path))
        if b is None or c is None:
            continue
        out.append(
            ScalarDrift(
                name=label,
                baseline=b,
                current=c,
                delta=round(c - b, 4),
                direction=_direction(spec, b, c),
            )
        )
    return out


def _ids_from_list(payload: dict, list_path: str, key: str) -> list[str]:
    """Extract the stable id (e.g. `qname`, `path`) from each entry of a
    nested list. Tolerates missing fields and non-list values."""
    raw = _walk(payload, list_path)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            v = entry.get(key)
            if isinstance(v, str):
                out.append(v)
    return out


def _set_drift(
    label: str,
    baseline: dict,
    current: dict,
    list_path: str,
    key: str,
) -> SetDrift | None:
    # If either side is missing the list entirely, skip the diff. We can't
    # tell whether the baseline was taken before this list existed
    # (tooling change) or simply has zero entries; treating "missing" as
    # "empty" would cause every current entry to look like a regression on
    # the first run after upgrading the tool.
    b_raw = _walk(baseline, list_path)
    c_raw = _walk(current, list_path)
    if not isinstance(b_raw, list) or not isinstance(c_raw, list):
        return None
    b_ids = _ids_from_list(baseline, list_path, key)
    c_ids = _ids_from_list(current, list_path, key)
    if not b_ids and not c_ids:
        return None
    b_set = set(b_ids)
    c_set = set(c_ids)
    return SetDrift(
        name=label,
        regressions=sorted(c_set - b_set),
        improvements=sorted(b_set - c_set),
        unchanged=len(b_set & c_set),
        baseline_size=len(b_set),
        current_size=len(c_set),
    )


def _cycle_set_drift(baseline: dict, current: dict) -> SetDrift | None:
    """Cycles are lists of files; key each cycle by its sorted file tuple."""
    b_raw = _walk(baseline, "cycles.groups")
    c_raw = _walk(current, "cycles.groups")
    if not isinstance(b_raw, list) or not isinstance(c_raw, list):
        return None

    def to_keys(payload: dict) -> list[str]:
        raw = _walk(payload, "cycles.groups")
        if not isinstance(raw, list):
            return []
        keys: list[str] = []
        for entry in raw:
            if isinstance(entry, dict):
                files = entry.get("files")
                if isinstance(files, list):
                    keys.append(" ↔ ".join(sorted(str(f) for f in files)))
        return keys

    b_keys = to_keys(baseline)
    c_keys = to_keys(current)
    if not b_keys and not c_keys:
        return None
    b_set, c_set = set(b_keys), set(c_keys)
    return SetDrift(
        name="import cycles",
        regressions=sorted(c_set - b_set),
        improvements=sorted(b_set - c_set),
        unchanged=len(b_set & c_set),
        baseline_size=len(b_set),
        current_size=len(c_set),
    )


def compute_drift(baseline: dict | None, current: dict) -> DriftReport:
    if baseline is None:
        return DriftReport(has_baseline=False)

    report = DriftReport(has_baseline=True)
    report.scalars = _scalar_drifts(baseline, current)

    set_specs: list[tuple[str, str, str]] = [
        ("complexity hotspots",  "complexity.hotspots",         "qname"),
        ("long functions",       "long_functions.functions",    "qname"),
        ("god files",            "god_files",                   "path"),
        ("client hubs (fan-out)", "fan_out.files",              "path"),
        ("dead code symbols",    "dead_code.symbols",           "qname"),
        ("hot unused-import files", "unused_imports.top_files", "path"),
        ("coverage gaps",        "coverage.gaps",               "qname"),
    ]
    for label, path, key in set_specs:
        sd = _set_drift(label, baseline, current, path, key)
        if sd is not None:
            report.sets.append(sd)
    cyc = _cycle_set_drift(baseline, current)
    if cyc is not None:
        report.sets.append(cyc)

    return report


def render_drift_markdown(report: DriftReport) -> str:
    """Render a single Markdown section ('## 10. Drift since baseline')."""
    if not report.has_baseline:
        return ""

    lines: list[str] = []
    lines.append("## 10. Drift since baseline")
    lines.append("")
    lines.append(
        f"- **{report.regression_count}** regression(s), "
        f"**{report.improvement_count}** improvement(s)"
    )
    lines.append("")

    if report.scalars:
        lines.append("### Scalar metrics")
        lines.append("")
        lines.append("| metric | baseline | current | delta | direction |")
        lines.append("|---|--:|--:|--:|---|")
        for s in report.scalars:
            arrow = {
                "improved":  "improved",
                "worsened":  "worsened",
                "unchanged": "—",
                "neutral":   "(neutral)",
            }.get(s.direction, s.direction)
            sign = "+" if s.delta > 0 else ""
            # numeric formatting: int when both ends are int, else 2dp
            if float(s.baseline).is_integer() and float(s.current).is_integer():
                b_str = f"{int(s.baseline)}"
                c_str = f"{int(s.current)}"
                d_str = f"{sign}{int(s.delta)}"
            else:
                b_str = f"{s.baseline:.2f}"
                c_str = f"{s.current:.2f}"
                d_str = f"{sign}{s.delta:.2f}"
            lines.append(f"| {s.name} | {b_str} | {c_str} | {d_str} | {arrow} |")
        lines.append("")

    for sd in report.sets:
        if not sd.regressions and not sd.improvements:
            continue
        lines.append(f"### {sd.name}")
        lines.append("")
        lines.append(
            f"- baseline: {sd.baseline_size} entries · "
            f"current: {sd.current_size} entries · "
            f"unchanged: {sd.unchanged}"
        )
        lines.append("")
        if sd.regressions:
            lines.append(f"**Newly in the list ({len(sd.regressions)} regression(s)):**")
            for entry in sd.regressions:
                lines.append(f"- `{entry}`")
            lines.append("")
        if sd.improvements:
            lines.append(f"**No longer in the list ({len(sd.improvements)} improvement(s)):**")
            for entry in sd.improvements:
                lines.append(f"- `{entry}`")
            lines.append("")

    if report.regression_count == 0 and report.improvement_count == 0:
        lines.append("_No measurable drift since baseline._")
        lines.append("")

    return "\n".join(lines)
