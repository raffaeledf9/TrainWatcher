"""History chart as approved: one step line per Train, legend "IT 9967  06:15 → 09:24", end prices labelled
inside the plot, € on every y tick, dashed max line when a max is set. Returns PNG bytes."""
import io
from datetime import datetime

from trainwatcher.i18n import EN, price

COLORS = ["#2481cc", "#e67e22", "#8e44ad", "#27ae60", "#c0392b", "#16a085"]


def chart_png(series, title, lang, max_price=None):
    """series: [(label, [(iso_datetime, price_or_None), ...])]; None marks 'not on sale' (a gap in the line)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    fig, ax = plt.subplots(figsize=(6, 3.6))
    last_x = None
    for (label, points), c in zip(series, COLORS):
        xs = [datetime.fromisoformat(a) for a, _ in points]
        ys = [p if p is not None else float("nan") for _, p in points]
        if not xs:
            continue
        xs.append(datetime.now()); ys.append(ys[-1])           # extend the current price to "now"
        ax.step(xs, ys, where="post", lw=1.8, color=c, label=label)
        if ys[-1] == ys[-1]:                                   # not NaN
            ax.annotate(price(ys[-1], lang), (xs[-1], ys[-1]), xytext=(-2, 3), textcoords="offset points", ha="right", va="bottom",
                        fontsize=7, color=c, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=.8))
        last_x = max(last_x or xs[-1], xs[-1])
    if max_price is not None:
        ax.axhline(max_price, ls="--", color="#d93025", lw=1, label=("max " if lang == EN else "massimo ") + price(max_price, lang))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: price(v, lang).replace(".00", "").replace(",00", "")))
    ax.set_title(title, fontsize=10, loc="left")
    ax.legend(fontsize=7, frameon=False, loc="upper right")
    ax.grid(alpha=.25)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
