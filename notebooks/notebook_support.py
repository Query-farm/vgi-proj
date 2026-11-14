"""Presentation helpers; projection analysis stays in SQL."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from IPython.display import Markdown, display

INK = "#223B43"
MUTED = "#61777C"
MERCATOR = "#CB7650"
GEODESIC = "#277D89"
UTM = "#66824D"
SOURCE = "Synthetic east–west endpoints · vgi-proj / PROJ via Haybarn + VGI · WGS84 ellipsoid"


def prose(value: str) -> None:
    """Display result-derived prose with the implementation folded."""
    display(Markdown(value))


def set_theme() -> None:
    """Apply shared plotting and table styles."""
    sns.set_theme(
        style="whitegrid",
        context="notebook",
        rc={
            "font.family": "DejaVu Sans",
            "figure.dpi": 150,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "grid.color": "#E5ECEC",
            "axes.edgecolor": "#CBD6D6",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titlelocation": "left",
            "axes.titleweight": "semibold",
            "legend.frameon": False,
            "text.parse_math": False,
        },
    )
    pd.set_option("display.max_columns", 14)
    pd.set_option("display.precision", 5)


def chart(fig: plt.Figure, title: str, subtitle: str) -> None:
    """Give each embedded figure a title, units context and provenance."""
    fig.text(0.08, 0.99, title, ha="left", va="top", fontsize=17, fontweight="semibold")
    fig.text(0.08, 0.915, subtitle, ha="left", va="top", fontsize=10, color=MUTED)
    sns.despine(fig=fig)
    fig.tight_layout(rect=(0, 0.09, 1, 0.83), w_pad=3)
    fig.text(0.08, 0.025, SOURCE, ha="left", va="bottom", fontsize=8.5, color=MUTED)
    plt.show()
