from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

State = Literal["ok", "warn", "bad"]
Mode = Literal["password", "google"]


class HealthItem(BaseModel):
    key: str
    label: str
    state: State
    detail: str


class FunnelStep(BaseModel):
    key: str
    label: str
    value: int
    note: str | None = None


class AlertChannel(BaseModel):
    channel: str
    sent: int
    failed: int


class JobItem(BaseModel):
    id: str
    label: str
    last_run: datetime | None
    status: Literal["ok", "error", "never"]
    duration_ms: int | None
    next_run: datetime | None


class IncidentListItem(BaseModel):
    id: uuid.UUID
    status: Literal["new", "in_progress", "resolved"]
    kind: str
    title: str
    context: str
    count: int
    affected_users: int
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None


class IncidentDetail(IncidentListItem):
    occurrences: list[datetime]
    users: list[str]
    where: str
    technical_cause: str
    user_message: str | None


class IncidentTiles(BaseModel):
    open: int
    new: int
    affected_users: int
    server_errors: int
    browser_errors: int
    resolved_period: int


class DayCount(BaseModel):
    date: str
    count: int


class IncidentsResponse(BaseModel):
    items: list[IncidentListItem]
    tiles: IncidentTiles
    per_day: list[DayCount]


class IncidentPatch(BaseModel):
    status: Literal["new", "in_progress", "resolved"]


class OverviewTiles(BaseModel):
    users_total: int
    users_new: int
    profiles_complete: int
    offers_30d: int
    offers_today: int
    analyses_today: int
    prefilter_today: int
    alerts_sent_today: int
    alerts_by_channel_today: dict[str, int]
    alerts_failed_today: int


class OffersBySource(BaseModel):
    sources: list[str]
    days: list[dict]


class OverviewResponse(BaseModel):
    generated_at: datetime
    health: list[HealthItem]
    tiles: OverviewTiles
    signups: list[DayCount]
    matching_funnel: list[FunnelStep]
    offers_by_source: OffersBySource
    alerts_today: list[AlertChannel]
    jobs: list[JobItem]
    recent_incidents: list[IncidentListItem]


class BehaviorTiles(BaseModel):
    active_users: int
    users_total: int
    page_views: int
    applications: int
    unverified: int
    cost_today_eur: float | None
    cost_period_eur: float | None


class SignupMode(BaseModel):
    key: Mode
    label: str
    count: int


class TopPage(BaseModel):
    path: str
    label: str
    count: int


class TopOffer(BaseModel):
    title: str
    company: str
    count: int
    avg_score: int | None


class CvOptimizedOffer(BaseModel):
    title: str
    company: str
    generated: int
    kept: int


class CvOptimizationStats(BaseModel):
    generated_today: int
    generated: int
    kept: int
    users: int
    per_user: float | None
    top_offers: list[CvOptimizedOffer]


class CostDay(BaseModel):
    date: str
    detailed_eur: float
    prefilter_eur: float


class AccountToFollow(BaseModel):
    user_id: uuid.UUID
    email: str
    created_at: datetime
    mode: Mode
    stage: Literal["unverified", "no_cv", "no_application"]
    stage_label: str
    action: Literal["resend_verification", "reminder"]


class BehaviorResponse(BaseModel):
    tracking_since: datetime | None
    tiles: BehaviorTiles
    funnel: list[FunnelStep]
    signup_modes: list[SignupMode]
    top_pages: list[TopPage]
    top_applied_offers: list[TopOffer]
    cv_optimization: CvOptimizationStats
    cost_daily: list[CostDay]
    cost_note: str
    accounts_to_follow: list[AccountToFollow]


class AccountAction(BaseModel):
    action: Literal["resend_verification", "reminder"]


class Detail(BaseModel):
    detail: str


class UserRow(BaseModel):
    user_id: uuid.UUID
    email: str
    first_name: str | None
    created_at: datetime
    mode: Mode
    verified: bool
    profile_status: Literal["none", "draft", "complete"]
    last_seen: datetime | None
    alerts_enabled: bool
    applications: int
    is_admin: bool


class Audience(BaseModel):
    key: str
    label: str
    count: int


class AnnouncementContent(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class AnnouncementPreview(BaseModel):
    subject: str
    text: str
    html: str


class AnnouncementSend(AnnouncementContent):
    audience: str
    expected_count: int


class AnnouncementSendResult(BaseModel):
    id: uuid.UUID
    sent: int
    skipped_unsubscribed: int


class AnnouncementHistoryItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    subject: str
    audience: str
    audience_label: str
    sent: int
    failed: int


class PageTrack(BaseModel):
    path: str = Field(min_length=1, max_length=300)


class ClientErrorTrack(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    path: str | None = Field(default=None, max_length=300)
    user_agent: str | None = Field(default=None, max_length=300)
    stack: str | None = Field(default=None, max_length=4000)


class UnsubscribeInfo(BaseModel):
    email: str
    already: bool


class UnsubscribeBody(BaseModel):
    token: str
