"""History chart: one step line per price history (trains with identical histories share a line and a legend
entry), legend below the plot so it never covers a line, end prices in a free margin right of "now" without
overlapping, € on every y tick, dashed max line when a max is set, Italian time on the x axis. Returns PNG bytes."""
import io
from datetime import datetime, timezone

from trainwatcher.i18n import EN, price

from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

COLORS = ["#2481cc", "#e67e22", "#8e44ad", "#27ae60", "#c0392b", "#16a085", "#d4ac0d", "#7f8c8d"]


def _local(utc_iso):
    """Observations are stored in UTC; the Owner reads Italian time."""
    return datetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc).astimezone(ROME).replace(tzinfo=None)


def _at(history, when):
    v = None
    for x, y in history:
        if x > when:
            break
        v = y
    return v


def _overlap(a, b):
    """True if two histories show the same price over some stretch of time (one line would hide the other)."""
    return any(_at(a, w) is not None and _at(a, w) == _at(b, w) for w in sorted({x for x, _ in a} | {x for x, _ in b}))


def _merge(series):
    """{history: [labels]}: trains with identical price histories share one line. Observations also record
    seat-count changes; only price changes count here, or equal lines wouldn't merge."""
    merged = {}
    for label, points in series:
        points = [p for i, p in enumerate(points) if i == 0 or p[1] != points[i - 1][1]]
        if points:
            merged.setdefault(tuple(points), []).append(label)
    return merged


def chart_png(series, title, lang, max_price=None):
    """series: [(label, [(utc_iso_datetime, price_or_None), ...])]; None marks 'not on sale' (a gap in the line)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    merged = _merge(series)
    now = datetime.now(ROME).replace(tzinfo=None)
    fig, ax = plt.subplots(figsize=(6, 3.4))
    ends, hist = [], list(merged)
    for i, (points, labels) in enumerate(merged.items()):
        c = COLORS[i % len(COLORS)]
        xs = [_local(a) for a, _ in points] + [now]            # the current price runs to "now"
        ys = [p if p is not None else float("nan") for _, p in points]
        ys.append(ys[-1])
        # a line that a later one overlaps is drawn wider, so where they coincide both colours stay visible
        under = min(sum(_overlap(points, h) for h in hist[i + 1:]), 3)
        ax.step(xs, ys, where="post", lw=2 + 1.6 * under, color=c, label="\n".join(labels), solid_capstyle="butt")
        if ys[-1] == ys[-1]:                                   # not NaN: still on sale
            ends.append((ys[-1], c))
    if max_price is not None:
        start = min((_local(p[0][0]) for p in merged), default=now)  # stops at "now": the margin is for the end prices
        ax.plot([start, now], [max_price, max_price], ls="--", color="#d93025", lw=1, label=("max " if lang == EN else "massimo ") + price(max_price, lang))
    ax.margins(x=0.02, y=0.12)
    x0, _ = ax.get_xlim()
    ax.set_xlim(x0, mdates.date2num(now) + (mdates.date2num(now) - x0) * 0.17)  # free margin for the end prices
    lo, hi = ax.get_ylim()
    last = None
    for y, c in sorted(ends):                                  # nudge end labels apart (bottom up) so none overlap
        pos = y if last is None else max(y, last + (hi - lo) * 0.075)
        ax.annotate(price(y, lang), (now, pos), xytext=(6, 0), textcoords="offset points", ha="left", va="center", fontsize=8,
                    color=c, fontweight="bold")
        ax.plot([now], [y], "o", ms=4, color=c)
        last = pos
    ax.set_ylim(lo, max(hi, (last or hi) + (hi - lo) * 0.05))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: price(v, lang).replace(".00", "").replace(",00", "")))
    loc = mdates.AutoDateLocator(minticks=3, maxticks=6)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(  # numeric dates: same in English and Italian
        loc, formats=["%Y", "%d/%m", "%d/%m", "%H:%M", "%H:%M", "%S"], zero_formats=["", "%Y", "%d/%m", "%d/%m", "%H:%M", "%H:%M"],
        offset_formats=["", "%Y", "%m/%Y", "%d/%m/%Y", "%d/%m/%Y", "%d/%m/%Y %H:%M"]))
    ax.tick_params(labelsize=8)
    ax.set_title(title, fontsize=10, loc="left")
    leg = ax.legend(fontsize=8, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.13), ncol=1, handlelength=1.6, labelspacing=0.5)
    for h in leg.legend_handles:
        if h.get_linestyle() == "-":
            h.set_linewidth(2.5)
    ax.grid(alpha=.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
