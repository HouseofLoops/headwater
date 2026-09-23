"""
Shared constants for the Headwater application.

This module contains constants that are used across multiple modules
to avoid code duplication and ensure consistency.
"""

# Keyword categories for Google Autocomplete variation generation
# Used by: google_autocomplete_service.py, google_autocomplete_api.py
KEYWORD_CATEGORIES = {
    "Questions": [
        "who",
        "what",
        "where",
        "when",
        "why",
        "how",
        "are",
        "can",
        "does",
        "did",
        "should",
        "would",
        "could",
        "is",
        "am",
        "might",
    ],
    "Prepositions": [
        "can",
        "with",
        "for",
        "by",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "under",
        "over",
        "within",
    ],
    "Alphabet": list("abcdefghijklmnopqrstuvwxyz"),
    "Comparisons": ["vs", "versus", "or", "compared to", "compared with", "against", "like", "similar to"],
    "Intent-Based": [
        "buy",
        "review",
        "price",
        "best",
        "top",
        "how to",
        "why to",
        "where to",
        "find",
        "get",
        "download",
        "install",
        "learn",
        "use",
        "compare",
        "donate",
        "subscribe",
        "sign up",
        "best way to",
    ],
    "Intent-Based - Transactional": ["buy", "purchase", "order", "book", "subscribe"],
    "Intent-Based - Informational": ["how to", "what is", "tips for", "guide to", "information about"],
    "Intent-Based - Navigational": ["official site", "login", "homepage", "contact"],
    "Time-Related": [
        "when",
        "schedule",
        "deadline",
        "today",
        "now",
        "latest",
        "future",
        "upcoming",
        "recently",
        "this week",
        "this month",
        "this year",
        "current",
        "historical",
        "past",
        "before",
        "after",
    ],
    "Audience-Specific": [
        "for beginners",
        "for small businesses",
        "for students",
        "for professionals",
        "for teachers",
        "for developers",
        "for marketers",
        "for educators",
        "for entrepreneurs",
        "for hobbyists",
        "for seniors",
        "for children",
        "for parents",
        "for freelancers",
        "for startups",
        "for non-profits",
    ],
    "Problem-Solving": [
        "solution",
        "issue",
        "error",
        "troubleshoot",
        "fix",
        "how to solve",
        "how to fix",
        "common problems",
        "tips for",
        "overcoming",
        "resolving",
        "addressing",
        "dealing with",
        "combating",
        "eliminating",
    ],
    "Feature-Specific": [
        "with video",
        "with images",
        "analytics",
        "tools",
        "with example",
        "with tutorials",
        "with guides",
        "with screenshots",
        "with templates",
        "with case studies",
        "for mobile",
        "for desktop",
        "with API",
        "with integrations",
        "with extensions",
        "customizable",
        "premium features",
        "advanced features",
    ],
    "Opinions/Reviews": [
        "review",
        "opinion",
        "rating",
        "feedback",
        "testimonial",
        "user reviews",
        "expert reviews",
        "customer reviews",
        "unbiased reviews",
        "honest opinions",
        "detailed ratings",
        "product testimonials",
        "service feedback",
        "peer reviews",
        "trusted reviews",
    ],
    "Cost-Related": [
        "price",
        "cost",
        "budget",
        "cheap",
        "expensive",
        "value",
        "affordable",
        "free",
        "discount",
        "promotions",
        "deals",
        "cheapest",
        "most affordable",
        "pricing plans",
        "cost-effective",
        "low cost",
        "premium price",
        "worth the price",
        "ROI",
    ],
    "Trend-Based": [
        "trends",
        "new",
        "upcoming",
        "latest",
        "hot",
        "viral",
        "popular",
        "current",
        "2024",
        "emerging",
        "now",
        "breakthrough",
    ],
    "Geographic-Specific": [
        "in New York",
        "near me",
        "US based",
        "local",
        "regional",
        "global",
        "Worldwide",
        "California",
        "Downtown",
    ],
    "Demographic-Specific": [
        "for seniors",
        "for millennials",
        "for Gen Z",
        "for men",
        "for women",
        "for families",
        "for singles",
        "for couples",
        "for retirees",
        "for parents",
        "for teenagers",
    ],
    "Seasonal/Event-Specific": [
        "during Christmas",
        "for Summer",
        "Black Friday 2024",
        "Cyber Monday 2024",
        "Halloween",
        "Spring",
        "Fall",
        "Back to School",
        "New Year",
        "Easter",
    ],
    "Problem/Need-Based": [
        "how to prevent",
        "how to manage",
        "how to improve",
        "how to reduce",
        "how to increase",
        "how to enhance",
        "alternatives to",
        "replacement for",
        "best practices for",
        "real-life examples of",
    ],
}

# Default categories to use when none specified
DEFAULT_KEYWORD_CATEGORIES = ["Questions", "Prepositions", "Alphabet"]


# -----------------------------------------------------------------------------
# User-Agent strings for HTTP requests
# Used by: http_client.py, google_trends_service.py, google_trends_api.py,
#          google_news_api.py
# -----------------------------------------------------------------------------

# Default User-Agent for the application
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; Headwater/1.0)"

# User-Agent strings for rotation (browser simulation)
USER_AGENTS = {
    "default": DEFAULT_USER_AGENT,
    "windows_chrome": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "mac_chrome": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "linux_chrome": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "windows_firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "mac_firefox": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:130.0) Gecko/20100101 Firefox/130.0",
    "linux_firefox": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "ipad": "Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "android": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
}

# List of User-Agent strings for rotation (used for scraping resilience)
USER_AGENT_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)",
    "Mozilla/5.0 (iPad; CPU OS 14_0 like Mac OS X)",
    "Mozilla/5.0 (Android 10; Mobile; rv:79.0)",
    "Mozilla/5.0 (Windows NT 6.1; WOW64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6)",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:92.0)",
    "Mozilla/5.0 (iPod touch; CPU iPhone OS 14_0 like Mac OS X)",
]

# List of Referer URLs for header rotation
REFERER_LIST = [
    "https://www.google.com/",
    "https://news.google.com/",
    "https://www.bing.com/",
    "https://www.yahoo.com/",
    "https://www.duckduckgo.com/",
    "https://www.ask.com/",
    "https://www.aol.com/",
    "https://www.ecosia.org/",
    "https://www.startpage.com/",
    "https://www.qwant.com/",
]


def get_user_agent(agent_type: str = "default") -> str:
    """
    Get a User-Agent string by type.

    Args:
        agent_type: Type of user agent to retrieve

    Returns:
        User-Agent string, defaults to DEFAULT_USER_AGENT if type not found
    """
    return USER_AGENTS.get(agent_type, DEFAULT_USER_AGENT)


def get_random_user_agent() -> str:
    """
    Get a random User-Agent string from the rotation list.

    Returns:
        Random User-Agent string
    """
    import random

    return random.choice(USER_AGENT_LIST)


def get_random_referer() -> str:
    """
    Get a random Referer URL from the rotation list.

    Returns:
        Random Referer URL
    """
    import random

    return random.choice(REFERER_LIST)
