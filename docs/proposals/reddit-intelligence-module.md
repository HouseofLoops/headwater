# Reddit Intelligence Module - Architecture Design

> **Status**: Planning
> **Version**: 1.0
> **Created**: 2024-12-28
> **Last Updated**: 2024-12-28

## Overview

The Reddit Intelligence Module is designed to discover and score high-value Reddit users across monitored subreddits. It uses a scraping-first approach (avoiding Reddit's commercial API licensing) with tiered monitoring to optimize resource usage and costs.

### Key Features

- **Multi-source data collection**: RSS feeds, Playwright browser automation, PullPush historical API
- **Intelligent user scoring**: 4-dimension scoring algorithm (Engagement, Influence, Expertise, Business Potential)
- **Tiered monitoring**: Hot (hourly), Warm (daily), Cold (weekly) with auto-promotion/demotion
- **Lead export**: CSV/JSON export with contact enrichment
- **Cost-efficient**: 88-96% savings vs Reddit Commercial API

---

## Table of Contents

1. [Cost Analysis](#cost-analysis)
2. [Module Structure](#module-structure)
3. [Data Collection Layer](#data-collection-layer)
4. [User Scoring Algorithm](#user-scoring-algorithm)
5. [Tiered Monitoring System](#tiered-monitoring-system)
6. [API Endpoints](#api-endpoints)
7. [Database Schema](#database-schema)
8. [Integration](#integration)
9. [Implementation Phases](#implementation-phases)

---

## Cost Analysis

### Webshare Static ISP Proxy Costs ($0.30/IP, Unlimited Bandwidth)

#### Per-Tier Costs

| Tier | Frequency | Subreddits | IPs Needed | Monthly Cost |
|------|-----------|------------|------------|--------------|
| **Hot** | Hourly | 10 | 10 | $3.00 |
| **Hot** | Hourly | 20 | 15 | $4.50 |
| **Warm** | Daily | 100 | 20 | $6.00 |
| **Warm** | Daily | 200 | 35 | $10.50 |
| **Cold** | Weekly | 500 | 35 | $10.50 |
| **Cold** | Weekly | 1,000 | 75 | $22.50 |

#### Combined Production Configurations

| Configuration | Hot | Warm | Cold | Total IPs | Monthly Cost |
|---------------|-----|------|------|-----------|--------------|
| **Starter** | 10 subs (hourly) | 50 subs (daily) | 200 subs (weekly) | 40 | **$12.00** |
| **Growth** | 20 subs (hourly) | 100 subs (daily) | 500 subs (weekly) | 60 | **$18.00** |
| **Scale** | 20 subs (hourly) | 200 subs (daily) | 1,000 subs (weekly) | 100 | **$30.00** |
| **Enterprise** | 50 subs (hourly) | 500 subs (daily) | 2,000 subs (weekly) | 175 | **$52.50** |

#### Comparison: Scraping vs Reddit Commercial API

| Configuration | Subreddits Covered | Scraping Cost | Reddit API Cost | **Savings** |
|---------------|-------------------|---------------|-----------------|-------------|
| **Starter** | 260 | $12/mo | $144.72/mo | **92%** |
| **Growth** | 620 | $18/mo | $340.20/mo | **95%** |
| **Scale** | 1,220 | $30/mo | $670.32/mo | **96%** |
| **Enterprise** | 2,550 | $52.50/mo | $1,400/mo | **96%** |

### Reddit API Limits (Reference)

- **OAuth**: 100 requests/minute
- **Unauthenticated**: 10 requests/minute
- **Free Tier**: 144,000 requests/day
- **Commercial**: $0.24 per 1,000 API calls
- **Note**: Internal company use is considered commercial by Reddit

---

## Module Structure

```
app/
├── api/
│   └── reddit/
│       ├── __init__.py
│       ├── router.py              # FastAPI router registration
│       ├── subreddits.py          # Subreddit management endpoints
│       ├── users.py               # User discovery/scoring endpoints
│       ├── monitoring.py          # Tier management endpoints
│       └── leads.py               # Lead export endpoints
│
├── services/
│   └── reddit/
│       ├── __init__.py
│       ├── reddit_service.py      # Main orchestration layer
│       ├── rss_collector.py       # RSS feed parser (free, unlimited)
│       ├── playwright_scraper.py  # Browser automation scraper
│       ├── pullpush_client.py     # Historical data API client
│       ├── user_scorer.py         # High-value user scoring
│       ├── contact_enricher.py    # Email/social discovery
│       └── tier_manager.py        # Hot/Warm/Cold tier logic
│
├── schemas/
│   └── reddit/
│       ├── __init__.py
│       ├── subreddit.py           # Subreddit models
│       ├── user.py                # Reddit user models
│       ├── post.py                # Post/comment models
│       ├── score.py               # Scoring models
│       └── monitoring.py          # Tier configuration models
│
├── models/
│   └── reddit.py                  # SQLAlchemy ORM models
│
└── workers/
    └── reddit/
        ├── __init__.py
        ├── monitor_worker.py      # Background monitoring service
        ├── scorer_worker.py       # Batch scoring processor
        └── enrichment_worker.py   # Contact discovery processor
```

---

## Data Collection Layer

### RSS Collector (Primary - Free & Unlimited)

```python
# app/services/reddit/rss_collector.py

class RedditRSSCollector:
    """
    Collects posts from Reddit RSS feeds.
    - No rate limits, no authentication required
    - Returns last 25 posts per feed
    - Supports: /new, /hot, /rising, /top
    """

    FEED_TYPES = ["new", "hot", "rising", "top"]

    async def fetch_subreddit_feed(
        self,
        subreddit: str,
        feed_type: str = "new"
    ) -> list[RedditPost]:
        """Fetch RSS feed for a subreddit."""
        url = f"https://www.reddit.com/r/{subreddit}/{feed_type}.rss"
        # Parse XML, extract posts, authors

    async def fetch_user_feed(self, username: str) -> list[RedditPost]:
        """Fetch RSS feed for a user's posts."""
        url = f"https://www.reddit.com/user/{username}.rss"

    async def monitor_subreddits(
        self,
        subreddits: list[str]
    ) -> dict[str, list[RedditPost]]:
        """Batch monitor multiple subreddits via RSS."""
```

### Playwright Scraper (Deep Data Extraction)

```python
# app/services/reddit/playwright_scraper.py

class RedditPlaywrightScraper:
    """
    Deep scraping using Playwright browser automation.
    - Used when RSS doesn't provide enough data
    - Extracts: karma breakdown, cake day, trophies, full history
    - Rotates through Webshare Static ISP proxies
    """

    def __init__(self, proxy_pool: ProxyPool):
        self.proxy_pool = proxy_pool
        self.browser_context = None

    async def scrape_user_profile(self, username: str) -> RedditUserProfile:
        """
        Scrape full user profile data.
        Returns: karma breakdown, account age, trophies, bio, links
        """

    async def scrape_user_posts(
        self,
        username: str,
        limit: int = 100
    ) -> list[RedditPost]:
        """Scrape user's post history with full metadata."""

    async def scrape_user_comments(
        self,
        username: str,
        limit: int = 100
    ) -> list[RedditComment]:
        """Scrape user's comment history."""

    async def scrape_subreddit_posts(
        self,
        subreddit: str,
        sort: str = "hot",
        time_filter: str = "week",
        limit: int = 100
    ) -> list[RedditPost]:
        """Deep scrape subreddit with all post metadata."""

    async def extract_contact_info(self, username: str) -> ContactInfo:
        """
        Extract contact information from user profile.
        - Bio links (Twitter, LinkedIn, website)
        - Pinned posts with contact info
        - Comment history mentions
        """
```

### PullPush Client (Historical Data)

```python
# app/services/reddit/pullpush_client.py

class PullPushClient:
    """
    Historical Reddit data via PullPush API.
    - Free, 15-30 requests/minute
    - Access deleted/removed content
    - Full historical search
    """

    BASE_URL = "https://api.pullpush.io"

    async def search_submissions(
        self,
        subreddit: str = None,
        author: str = None,
        q: str = None,
        after: int = None,  # Unix timestamp
        before: int = None,
        size: int = 100
    ) -> list[RedditPost]:
        """Search historical submissions."""

    async def search_comments(
        self,
        subreddit: str = None,
        author: str = None,
        q: str = None,
        size: int = 100
    ) -> list[RedditComment]:
        """Search historical comments."""

    async def get_user_history(
        self,
        username: str,
        months_back: int = 12
    ) -> UserHistory:
        """Get complete user history for scoring."""
```

---

## User Scoring Algorithm

### Score Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| **Engagement** | 25% | How actively the user participates |
| **Influence** | 25% | Reach and impact of their content |
| **Expertise** | 25% | Domain knowledge in target subreddits |
| **Business** | 25% | Potential as a business lead |

### Score Tiers

| Tier | Score Range | Description |
|------|-------------|-------------|
| **Platinum** | 80-100 | Top-tier high-value users |
| **Gold** | 60-79 | Strong candidates for outreach |
| **Silver** | 40-59 | Moderate value, worth monitoring |
| **Bronze** | 0-39 | Low priority |

### Implementation

```python
# app/services/reddit/user_scorer.py

from dataclasses import dataclass
from enum import Enum

class ValueCategory(Enum):
    ENGAGEMENT = "engagement"
    INFLUENCE = "influence"
    EXPERTISE = "expertise"
    BUSINESS = "business"

@dataclass
class ScoreWeights:
    engagement: float = 0.25
    influence: float = 0.25
    expertise: float = 0.25
    business: float = 0.25

class RedditUserScorer:
    """
    Scores Reddit users on multiple value dimensions.
    Each dimension scored 0-100, weighted for final score.
    """

    def __init__(self, weights: ScoreWeights = None):
        self.weights = weights or ScoreWeights()

    def calculate_engagement_score(self, user: RedditUser) -> float:
        """
        Engagement Score (0-100):
        - Karma accumulation rate (40%)
        - Posting frequency (30%)
        - Comment engagement received (30%)
        """
        karma_rate = min(user.karma_per_month / 5000, 1.0) * 40
        post_freq = min(user.posts_per_month / 15, 1.0) * 30
        engagement = min(user.avg_comments_per_post / 20, 1.0) * 30
        return karma_rate + post_freq + engagement

    def calculate_influence_score(self, user: RedditUser) -> float:
        """
        Influence Score (0-100):
        - Follower count (25%)
        - Average upvotes per post (35%)
        - Cross-posting reach (20%)
        - Award karma received (20%)
        """
        followers = min(user.followers / 1000, 1.0) * 25
        avg_upvotes = min(user.avg_post_score / 500, 1.0) * 35
        reach = min(user.unique_subreddits / 20, 1.0) * 20
        awards = min(user.award_karma / 1000, 1.0) * 20
        return followers + avg_upvotes + reach + awards

    def calculate_expertise_score(
        self,
        user: RedditUser,
        target_subreddits: list[str]
    ) -> float:
        """
        Domain Expertise Score (0-100):
        - Activity concentration in target subs (40%)
        - Quality ratio (upvote/downvote) (30%)
        - Helpful awards received (15%)
        - Account age in domain (15%)
        """
        concentration = self._calc_subreddit_concentration(
            user, target_subreddits
        ) * 40
        quality = min(user.upvote_ratio / 0.9, 1.0) * 30
        helpful = min(user.helpful_awards / 10, 1.0) * 15
        tenure = min(user.domain_months / 24, 1.0) * 15
        return concentration + quality + helpful + tenure

    def calculate_business_score(self, user: RedditUser) -> float:
        """
        Business Potential Score (0-100):
        - Has contact info available (30%)
        - Professional subreddit activity (25%)
        - Content indicates decision-maker (25%)
        - Verified/premium status (20%)
        """
        contact = 30 if user.has_contact_info else 0
        professional = self._calc_professional_activity(user) * 25
        decision_maker = self._analyze_decision_signals(user) * 25
        verified = 20 if user.is_verified or user.is_premium else 0
        return contact + professional + decision_maker + verified

    def calculate_total_score(
        self,
        user: RedditUser,
        target_subreddits: list[str] = None
    ) -> UserScore:
        """Calculate weighted total score with breakdown."""
        engagement = self.calculate_engagement_score(user)
        influence = self.calculate_influence_score(user)
        expertise = self.calculate_expertise_score(user, target_subreddits or [])
        business = self.calculate_business_score(user)

        total = (
            engagement * self.weights.engagement +
            influence * self.weights.influence +
            expertise * self.weights.expertise +
            business * self.weights.business
        )

        return UserScore(
            username=user.username,
            total_score=total,
            engagement_score=engagement,
            influence_score=influence,
            expertise_score=expertise,
            business_score=business,
            tier=self._determine_tier(total)
        )

    def _determine_tier(self, score: float) -> str:
        if score >= 80:
            return "platinum"
        elif score >= 60:
            return "gold"
        elif score >= 40:
            return "silver"
        else:
            return "bronze"
```

---

## Tiered Monitoring System

### Tier Configuration

| Tier | Interval | Max Subreddits | Scrape Depth | Use Case |
|------|----------|----------------|--------------|----------|
| **Hot** | 1 hour | 50 | Medium | Core industry subreddits |
| **Warm** | 24 hours | 500 | Medium | Related niches |
| **Cold** | 7 days | 5,000 | Shallow | Discovery & long-tail |

### Implementation

```python
# app/services/reddit/tier_manager.py

from enum import Enum
from datetime import timedelta

class MonitoringTier(Enum):
    HOT = "hot"      # Hourly monitoring
    WARM = "warm"    # Daily monitoring
    COLD = "cold"    # Weekly monitoring

@dataclass
class TierConfig:
    tier: MonitoringTier
    interval: timedelta
    max_subreddits: int
    scrape_depth: str  # "shallow", "medium", "deep"

TIER_CONFIGS = {
    MonitoringTier.HOT: TierConfig(
        tier=MonitoringTier.HOT,
        interval=timedelta(hours=1),
        max_subreddits=50,
        scrape_depth="medium"
    ),
    MonitoringTier.WARM: TierConfig(
        tier=MonitoringTier.WARM,
        interval=timedelta(days=1),
        max_subreddits=500,
        scrape_depth="medium"
    ),
    MonitoringTier.COLD: TierConfig(
        tier=MonitoringTier.COLD,
        interval=timedelta(weeks=1),
        max_subreddits=5000,
        scrape_depth="shallow"
    ),
}

class TierManager:
    """
    Manages subreddit monitoring tiers with auto-promotion/demotion.
    """

    def __init__(self, db: Database):
        self.db = db

    async def assign_tier(
        self,
        subreddit: str,
        tier: MonitoringTier
    ) -> SubredditMonitor:
        """Assign a subreddit to a monitoring tier."""

    async def promote_subreddit(self, subreddit: str) -> MonitoringTier:
        """Promote subreddit to higher frequency tier."""
        current = await self.get_tier(subreddit)
        if current == MonitoringTier.COLD:
            return await self.assign_tier(subreddit, MonitoringTier.WARM)
        elif current == MonitoringTier.WARM:
            return await self.assign_tier(subreddit, MonitoringTier.HOT)
        return current

    async def demote_subreddit(self, subreddit: str) -> MonitoringTier:
        """Demote subreddit to lower frequency tier."""

    async def auto_adjust_tiers(self) -> TierAdjustmentReport:
        """
        Automatically adjust tiers based on activity.
        Promotes: High new user discovery rate
        Demotes: Low activity, few high-value users found
        """

    async def get_due_subreddits(
        self,
        tier: MonitoringTier
    ) -> list[SubredditMonitor]:
        """Get subreddits due for monitoring in a tier."""

    async def get_tier_stats(self) -> dict[MonitoringTier, TierStats]:
        """Get statistics for each monitoring tier."""
```

### Monitoring Worker

```python
# app/workers/reddit/monitor_worker.py

class RedditMonitorWorker:
    """
    Background worker that processes monitoring queue.
    Runs continuously, checking each tier's schedule.
    """

    def __init__(
        self,
        tier_manager: TierManager,
        rss_collector: RedditRSSCollector,
        scraper: RedditPlaywrightScraper,
        scorer: RedditUserScorer,
        db: Database
    ):
        self.tier_manager = tier_manager
        self.rss_collector = rss_collector
        self.scraper = scraper
        self.scorer = scorer
        self.db = db

    async def run(self):
        """Main worker loop."""
        while True:
            await self.process_hot_tier()
            await self.process_warm_tier()
            await self.process_cold_tier()
            await asyncio.sleep(60)  # Check every minute

    async def process_hot_tier(self):
        """Process hourly monitoring for hot tier."""
        due = await self.tier_manager.get_due_subreddits(MonitoringTier.HOT)
        for sub in due:
            await self.monitor_subreddit(sub, depth="medium")

    async def monitor_subreddit(
        self,
        sub: SubredditMonitor,
        depth: str
    ):
        """
        Full monitoring cycle for a subreddit:
        1. Fetch new posts via RSS
        2. Extract unique authors
        3. Score new users
        4. Deep scrape high-potential users
        5. Update database
        """
        # Step 1: Get new posts
        posts = await self.rss_collector.fetch_subreddit_feed(
            sub.name, "new"
        )

        # Step 2: Extract authors
        authors = {post.author for post in posts}
        new_authors = await self.db.filter_new_users(authors)

        # Step 3: Quick score via available data
        for author in new_authors:
            basic_data = await self.rss_collector.fetch_user_feed(author)
            quick_score = self.scorer.quick_score(basic_data)

            # Step 4: Deep scrape if promising
            if quick_score > 50:
                full_profile = await self.scraper.scrape_user_profile(author)
                full_score = self.scorer.calculate_total_score(full_profile)
                await self.db.save_scored_user(full_profile, full_score)

        # Update last checked timestamp
        await self.tier_manager.mark_checked(sub)
```

---

## API Endpoints

### Router Registration

```python
# app/api/reddit/router.py

from fastapi import APIRouter

reddit_router = APIRouter(prefix="/api/v1/reddit", tags=["Reddit"])

# Include sub-routers
reddit_router.include_router(subreddits_router)
reddit_router.include_router(users_router)
reddit_router.include_router(monitoring_router)
reddit_router.include_router(leads_router)
```

### Endpoint Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Subreddits** | | |
| POST | `/api/v1/reddit/subreddits/add` | Add subreddit to monitoring |
| DELETE | `/api/v1/reddit/subreddits/{name}` | Remove from monitoring |
| GET | `/api/v1/reddit/subreddits` | List monitored subreddits |
| GET | `/api/v1/reddit/subreddits/{name}/stats` | Get subreddit statistics |
| **Users** | | |
| GET | `/api/v1/reddit/users/search` | Search discovered users |
| GET | `/api/v1/reddit/users/{username}` | Get user profile & scores |
| GET | `/api/v1/reddit/users/{username}/history` | Get user activity history |
| POST | `/api/v1/reddit/users/score` | Manually score a user |
| **Monitoring** | | |
| GET | `/api/v1/reddit/monitoring/tiers` | Get tier configurations |
| PUT | `/api/v1/reddit/monitoring/tiers/{subreddit}` | Change subreddit tier |
| GET | `/api/v1/reddit/monitoring/status` | Get monitoring queue status |
| POST | `/api/v1/reddit/monitoring/run` | Trigger manual monitoring run |
| **Leads** | | |
| GET | `/api/v1/reddit/leads` | Get high-value user leads |
| GET | `/api/v1/reddit/leads/export` | Export leads (CSV/JSON) |
| POST | `/api/v1/reddit/leads/enrich` | Enrich leads with contact info |

### Example Endpoint Implementations

```python
# app/api/reddit/users.py

from fastapi import APIRouter, Query, Depends
from app.schemas.reddit.user import UserSearchParams, UserResponse
from app.services.reddit import RedditService

router = APIRouter()

@router.get("/users/search", response_model=list[UserResponse])
async def search_users(
    min_score: float = Query(0, ge=0, le=100),
    tier: str = Query(None, regex="^(platinum|gold|silver|bronze)$"),
    subreddit: str = Query(None),
    has_contact: bool = Query(None),
    sort_by: str = Query("total_score"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: RedditService = Depends(get_reddit_service)
):
    """
    Search discovered Reddit users with filtering.

    - **min_score**: Minimum total score (0-100)
    - **tier**: Filter by score tier (platinum/gold/silver/bronze)
    - **subreddit**: Filter by active subreddit
    - **has_contact**: Only users with discovered contact info
    - **sort_by**: Sort field (total_score, engagement, influence, etc.)
    """
    return await service.search_users(
        min_score=min_score,
        tier=tier,
        subreddit=subreddit,
        has_contact=has_contact,
        sort_by=sort_by,
        limit=limit,
        offset=offset
    )

@router.get("/leads/export")
async def export_leads(
    format: str = Query("csv", regex="^(csv|json)$"),
    min_score: float = Query(60),
    include_contact: bool = Query(True),
    service: RedditService = Depends(get_reddit_service)
):
    """
    Export high-value leads for outreach.
    Returns downloadable CSV or JSON file.
    """
    leads = await service.get_leads(
        min_score=min_score,
        include_contact=include_contact
    )

    if format == "csv":
        return StreamingResponse(
            generate_csv(leads),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=reddit_leads.csv"}
        )
    return leads
```

---

## Database Schema

```sql
-- ============================================================================
-- Reddit Intelligence Module - Database Schema
-- ============================================================================

-- Monitored Subreddits
CREATE TABLE reddit_subreddits (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    subscribers INTEGER,
    monitoring_tier VARCHAR(10) NOT NULL DEFAULT 'cold',
    last_checked_at TIMESTAMP,
    next_check_at TIMESTAMP,
    total_users_found INTEGER DEFAULT 0,
    high_value_users INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_subreddits_tier ON reddit_subreddits(monitoring_tier);
CREATE INDEX idx_subreddits_next_check ON reddit_subreddits(next_check_at);

-- Discovered Reddit Users
CREATE TABLE reddit_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,

    -- Profile Data
    account_created_at TIMESTAMP,
    total_karma INTEGER,
    post_karma INTEGER,
    comment_karma INTEGER,
    is_premium BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT FALSE,
    has_verified_email BOOLEAN,

    -- Scores (0-100)
    engagement_score FLOAT,
    influence_score FLOAT,
    expertise_score FLOAT,
    business_score FLOAT,
    total_score FLOAT,
    score_tier VARCHAR(10),  -- platinum, gold, silver, bronze

    -- Contact Info
    email VARCHAR(255),
    website VARCHAR(500),
    twitter_handle VARCHAR(50),
    linkedin_url VARCHAR(255),
    other_socials JSONB DEFAULT '{}',

    -- Activity
    active_subreddits TEXT[],
    top_subreddits TEXT[],
    posts_per_month FLOAT,
    comments_per_month FLOAT,

    -- Metadata
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_at TIMESTAMP,
    last_scored_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_score ON reddit_users(total_score DESC);
CREATE INDEX idx_users_tier ON reddit_users(score_tier);
CREATE INDEX idx_users_subreddits ON reddit_users USING GIN(active_subreddits);
CREATE INDEX idx_users_has_contact ON reddit_users((email IS NOT NULL OR website IS NOT NULL));

-- User Posts (for analysis)
CREATE TABLE reddit_posts (
    id SERIAL PRIMARY KEY,
    reddit_id VARCHAR(20) UNIQUE NOT NULL,
    user_id INTEGER REFERENCES reddit_users(id),
    subreddit VARCHAR(50) NOT NULL,
    title TEXT,
    score INTEGER,
    upvote_ratio FLOAT,
    num_comments INTEGER,
    created_at TIMESTAMP,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_posts_user ON reddit_posts(user_id);
CREATE INDEX idx_posts_subreddit ON reddit_posts(subreddit);

-- User Comments (for analysis)
CREATE TABLE reddit_comments (
    id SERIAL PRIMARY KEY,
    reddit_id VARCHAR(20) UNIQUE NOT NULL,
    user_id INTEGER REFERENCES reddit_users(id),
    post_id INTEGER REFERENCES reddit_posts(id),
    subreddit VARCHAR(50) NOT NULL,
    body TEXT,
    score INTEGER,
    created_at TIMESTAMP,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_comments_user ON reddit_comments(user_id);
CREATE INDEX idx_comments_subreddit ON reddit_comments(subreddit);

-- Monitoring History
CREATE TABLE reddit_monitoring_logs (
    id SERIAL PRIMARY KEY,
    subreddit_id INTEGER REFERENCES reddit_subreddits(id),
    tier VARCHAR(10),
    posts_found INTEGER,
    new_users_found INTEGER,
    high_value_users_found INTEGER,
    duration_seconds FLOAT,
    status VARCHAR(20),  -- success, error, partial
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_monitoring_logs_subreddit ON reddit_monitoring_logs(subreddit_id);
CREATE INDEX idx_monitoring_logs_created ON reddit_monitoring_logs(created_at);

-- Lead Export History
CREATE TABLE reddit_lead_exports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,  -- Admin user who exported
    filter_criteria JSONB,
    total_leads INTEGER,
    format VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Subreddit-User Association (many-to-many)
CREATE TABLE reddit_user_subreddits (
    user_id INTEGER REFERENCES reddit_users(id),
    subreddit_id INTEGER REFERENCES reddit_subreddits(id),
    post_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    first_seen_at TIMESTAMP,
    last_active_at TIMESTAMP,
    PRIMARY KEY (user_id, subreddit_id)
);

CREATE INDEX idx_user_subreddits_user ON reddit_user_subreddits(user_id);
CREATE INDEX idx_user_subreddits_subreddit ON reddit_user_subreddits(subreddit_id);
```

---

## Integration

### Configuration

```python
# app/core/config.py - Add Reddit configuration

class Settings(BaseSettings):
    # ... existing settings ...

    # Reddit Module
    REDDIT_ENABLED: bool = True
    REDDIT_RSS_TIMEOUT: int = 30
    REDDIT_SCRAPE_TIMEOUT: int = 60
    REDDIT_MAX_CONCURRENT_SCRAPES: int = 5

    # Tiered Monitoring
    REDDIT_HOT_TIER_INTERVAL_HOURS: int = 1
    REDDIT_WARM_TIER_INTERVAL_HOURS: int = 24
    REDDIT_COLD_TIER_INTERVAL_HOURS: int = 168  # 1 week

    # Scoring Thresholds
    REDDIT_PLATINUM_THRESHOLD: float = 80.0
    REDDIT_GOLD_THRESHOLD: float = 60.0
    REDDIT_SILVER_THRESHOLD: float = 40.0

    # Proxy Configuration (uses existing Webshare config)
    # PROXY_URL, ENABLE_PROXY already defined
```

### Router Registration

```python
# app/api/__init__.py

from app.api.reddit.router import reddit_router

def register_routers(app: FastAPI):
    # ... existing routers ...
    app.include_router(google_maps_router)
    app.include_router(google_news_router)
    app.include_router(google_trends_router)
    app.include_router(google_autocomplete_router)
    app.include_router(youtube_transcripts_router)

    # New Reddit router
    app.include_router(reddit_router)
```

### Health Check Integration

```python
# Add to existing health check

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": __version__,
        "services": {
            "database": await check_database(),
            "redis": await check_redis(),
            "reddit_monitor": await check_reddit_monitor(),  # New
        }
    }
```

### Environment Variables

```env
# .env - Reddit Module Configuration

# Enable/Disable Reddit Module
REDDIT_ENABLED=true

# Monitoring Intervals (hours)
REDDIT_HOT_TIER_INTERVAL_HOURS=1
REDDIT_WARM_TIER_INTERVAL_HOURS=24
REDDIT_COLD_TIER_INTERVAL_HOURS=168

# Scoring Thresholds
REDDIT_PLATINUM_THRESHOLD=80.0
REDDIT_GOLD_THRESHOLD=60.0
REDDIT_SILVER_THRESHOLD=40.0

# Scraping Configuration
REDDIT_RSS_TIMEOUT=30
REDDIT_SCRAPE_TIMEOUT=60
REDDIT_MAX_CONCURRENT_SCRAPES=5

# Proxy (Webshare Static ISP recommended)
ENABLE_PROXY=true
PROXY_URL=http://username:password@proxy.webshare.io:80
```

---

## Implementation Phases

| Phase | Components | Description |
|-------|------------|-------------|
| **Phase 1** | RSS Collector, Basic User Model, Database Schema | Foundation - Core data collection |
| **Phase 2** | Playwright Scraper, Proxy Integration | Deep data extraction with rotation |
| **Phase 3** | Scoring Algorithm, User Scorer Service | Intelligence layer |
| **Phase 4** | Tier Manager, Monitoring Worker | Automated monitoring system |
| **Phase 5** | API Endpoints, Lead Export | User-facing interface |
| **Phase 6** | Contact Enricher, PullPush Integration | Enhanced data collection |

### Phase 1: Foundation
- [ ] Create database migrations
- [ ] Implement RSS collector service
- [ ] Create Pydantic schemas
- [ ] Basic SQLAlchemy models

### Phase 2: Data Collection
- [ ] Playwright scraper with proxy rotation
- [ ] User profile extraction
- [ ] Post/comment history scraping
- [ ] Rate limiting and retry logic

### Phase 3: Intelligence
- [ ] Scoring algorithm implementation
- [ ] Score calculation endpoints
- [ ] Tier classification
- [ ] Score history tracking

### Phase 4: Automation
- [ ] Tier manager service
- [ ] Background monitoring worker
- [ ] Auto-promotion/demotion logic
- [ ] Monitoring queue management

### Phase 5: User Interface
- [ ] FastAPI endpoints
- [ ] Search and filtering
- [ ] Lead export (CSV/JSON)
- [ ] Swagger documentation

### Phase 6: Enhancement
- [ ] Contact information extraction
- [ ] PullPush historical data integration
- [ ] Enhanced scoring with historical context
- [ ] Batch enrichment processing

---

## Legal Considerations

### Scraping Approach Justification

1. **Public Data**: Only scraping publicly accessible pages (no login required)
2. **Meta v. Bright Data Precedent**: Court ruled scraping public pages without login is defensible
3. **No ToS Violation**: Not using Reddit's authenticated API
4. **Rate Limiting**: Respectful scraping with delays and proxy rotation
5. **Internal Use**: Data used for internal business intelligence, not resold

### Best Practices

- Respect `robots.txt` directives
- Implement reasonable delays between requests
- Use rotating proxies to distribute load
- Don't scrape private or authenticated content
- Store data securely with access controls

---

## References

- [Webshare Proxy](https://www.webshare.io/?referral_code=o116umkbm8da) - Recommended proxy provider
- [PullPush API](https://pullpush.io/) - Historical Reddit data
- [Reddit RSS Feeds](https://www.reddit.com/wiki/rss) - Official RSS documentation
- [Playwright Python](https://playwright.dev/python/) - Browser automation
