"""Builds the content of a market-watch digest from the weekly counts.

Pure functions (no database, no AI): everything shown in the e-mail is a
count read from the history, or a plain sentence built from those counts --
nothing is invented. In particular the "plan d'action" never names a course
or a provider, only the skills that are asked for and missing from the CV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.modules.market.classifier import skill_key
from app.modules.market.taxonomy import SKILL_CATEGORY_LABELS

TOP_SKILLS = 8
EVOLUTION_SKILLS = 3
ACTION_ITEMS = 3
MAX_WEEKS = 8
# A share measured on fewer offers than this is too noisy to show a trend.
MIN_OFFERS_FOR_TREND = 20
# A missing skill must be asked for by at least this share of offers to make
# it into the action plan.
MIN_SHARE_FOR_ACTION = 0.10
MIN_WEEKS_FOR_FORECAST = 3


@dataclass
class WeekCounts:
    week_start: date
    offers_total: int
    is_complete: bool
    # skill_key -> (label, category, offers_with_skill)
    skills: dict[str, tuple[str, str, int]] = field(default_factory=dict)


@dataclass
class SkillRow:
    name: str
    category: str
    category_label: str
    share_pct: int
    trend_pts: int | None  # change vs the previous week, None when unknown
    in_cv: bool


@dataclass
class EvolutionRow:
    name: str
    weeks: list[tuple[date, int]]  # (week_start, share in %)
    first_pct: int
    last_pct: int


@dataclass
class DigestData:
    first_name: str | None
    label: str  # the specialty (or family) the digest is about
    level: str  # "specialty" | "family"
    week_start: date
    offers_analysed: int  # over the weeks used
    offers_latest_week: int
    weeks_used: int
    partial_week: bool
    top_skills: list[SkillRow]
    evolution: list[EvolutionRow]
    actions: list[str]
    forecasts: list[str]


def share(count: int, total: int) -> float:
    return count / total if total else 0.0


def pct(value: float) -> int:
    return round(value * 100)


def cv_skill_keys(profile_skills: list[str], free_text: str) -> tuple[set[str], str]:
    """(exact skill keys the profile lists, squashed free text) -- see
    `has_skill`."""
    exact = {skill_key(s) for s in profile_skills if isinstance(s, str) and s.strip()}
    squashed = skill_key(free_text)
    return exact, squashed


def has_skill(key: str, exact: set[str], squashed: str) -> bool:
    if key in exact:
        return True
    # Short keys ("r", "c", "go") would match inside any word: only an exact
    # listing counts for them.
    return len(key) >= 4 and key in squashed


def build_digest(
    *,
    weeks: list[WeekCounts],
    label: str,
    level: str,
    first_name: str | None,
    cv_exact: set[str],
    cv_squashed: str,
) -> DigestData | None:
    """`weeks`: the specialty's weekly counts, oldest first, already limited
    to the weeks to use. None when there is nothing to show."""
    weeks = [w for w in weeks if w.offers_total > 0][-MAX_WEEKS:]
    if not weeks:
        return None
    latest = weeks[-1]
    previous = weeks[-2] if len(weeks) >= 2 else None

    window_total = sum(w.offers_total for w in weeks[-4:])
    # Everything shown as a share is the LATEST week's, so the list, the
    # action plan and the last point of each curve always agree. The window
    # only feeds the "offers analysed" figure and the forecasts.
    labels: dict[str, tuple[str, str]] = {}
    for w in weeks[-4:]:
        for key, (name, category, _count) in w.skills.items():
            labels.setdefault(key, (name, category))
    latest_counts = {k: v[2] for k, v in latest.skills.items()}
    ranked = sorted(latest_counts, key=lambda k: (-latest_counts[k], k))

    def trend(key: str) -> int | None:
        if previous is None:
            return None
        if min(latest.offers_total, previous.offers_total) < MIN_OFFERS_FOR_TREND:
            return None
        now = share(latest.skills.get(key, ("", "", 0))[2], latest.offers_total)
        before = share(previous.skills.get(key, ("", "", 0))[2], previous.offers_total)
        return pct(now) - pct(before)

    top: list[SkillRow] = []
    for key in ranked[:TOP_SKILLS]:
        name, category = labels[key]
        top.append(
            SkillRow(
                name=name,
                category=category,
                category_label=SKILL_CATEGORY_LABELS.get(category, category),
                share_pct=pct(share(latest_counts[key], latest.offers_total)),
                trend_pts=trend(key),
                in_cv=has_skill(key, cv_exact, cv_squashed),
            )
        )

    evolution: list[EvolutionRow] = []
    if len(weeks) >= 2:
        for key in ranked[:EVOLUTION_SKILLS]:
            points = [
                (
                    w.week_start,
                    pct(share(w.skills.get(key, ("", "", 0))[2], w.offers_total)),
                )
                for w in weeks
            ]
            evolution.append(
                EvolutionRow(
                    name=labels[key][0],
                    weeks=points,
                    first_pct=points[0][1],
                    last_pct=points[-1][1],
                )
            )

    actions: list[str] = []
    for key in ranked:
        if len(actions) >= ACTION_ITEMS:
            break
        value = share(latest_counts[key], latest.offers_total)
        if value < MIN_SHARE_FOR_ACTION:
            break
        if has_skill(key, cv_exact, cv_squashed):
            continue
        name, category = labels[key]
        t = trend(key)
        rising = f", en hausse de {t} points" if t is not None and t > 0 else ""
        base = f"{name} est demandé dans {pct(value)} % des offres{rising}."
        if category == "certification":
            actions.append(
                f"{base} Il n'apparaît pas dans ton profil : envisage cette "
                "certification, ou ajoute-la à ton CV si tu l'as déjà."
            )
        else:
            actions.append(
                f"{base} Il n'apparaît pas dans ton profil : monte en "
                "compétence dessus, ou ajoute-le à ton CV si tu le maîtrises."
            )

    forecasts: list[str] = []
    if len(weeks) >= MIN_WEEKS_FOR_FORECAST:
        for key in ranked:
            if len(forecasts) >= 2:
                break
            series = [
                share(w.skills.get(key, ("", "", 0))[2], w.offers_total)
                for w in weeks[-MIN_WEEKS_FOR_FORECAST - 1 :]
            ]
            rising_run = all(b > a for a, b in zip(series, series[1:], strict=False))
            if rising_run and len(series) > 2 and pct(series[-1]) > pct(series[0]):
                forecasts.append(
                    f"{labels[key][0]} progresse depuis {len(series) - 1} "
                    f"semaines ({pct(series[0])} % → {pct(series[-1])} % des "
                    "offres) : une compétence à surveiller."
                )

    return DigestData(
        first_name=first_name,
        label=label,
        level=level,
        week_start=latest.week_start,
        offers_analysed=window_total,
        offers_latest_week=latest.offers_total,
        weeks_used=len(weeks[-4:]),
        partial_week=not latest.is_complete,
        top_skills=top,
        evolution=evolution,
        actions=actions,
        forecasts=forecasts,
    )
