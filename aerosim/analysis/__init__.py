"""Analysis tools that read an aircraft data package and grade it.

Nothing in here flies the aircraft in anger; it interrogates the same models
the simulator uses and reports what they imply about the design.
"""

from .design import DesignReport, design_report, format_report
from .modes import Mode, linearise, natural_modes

__all__ = [
    "DesignReport",
    "Mode",
    "design_report",
    "format_report",
    "linearise",
    "natural_modes",
]
