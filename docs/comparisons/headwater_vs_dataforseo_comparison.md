# Headwater vs DataForSEO: Feature Comparison

**Generated:** December 28, 2025

A detailed comparison of Google Maps data extraction capabilities between Headwater (self-hosted) and DataForSEO (cloud API).

---

## Quick Overview

| Aspect | Headwater | DataForSEO |
|--------|--------------|------------|
| **Type** | Self-hosted (Docker) | Cloud API |
| **Cost Model** | Proxy data only | Pay-per-request |
| **Cost per 1K businesses** | **$0.045** (with proxy) | $0.60 - $2.00 |
| **Cost Advantage** | **13-44x cheaper** | Baseline |
| **Rate Limits** | None (self-imposed) | 2,000 requests/min |
| **Results per Search** | Unlimited (pagination) | Up to 700 per area |
| **Turnaround** | Real-time | 6 sec - 45 min |

---

## Pricing Comparison

### DataForSEO Pricing

| Queue Type | Cost per Request | Cost per 1K | Turnaround |
|------------|------------------|-------------|------------|
| Standard | $0.0006 | $0.60 | ~5 minutes |
| Priority | $0.0012 | $1.20 | ~1 minute |
| Live | $0.002 | $2.00 | ~6 seconds |

**Cost Multipliers:**
- Search operators (site:, filetype:): 5x per operator
- `calculate_rectangles` parameter: 2x
- `depth` parameter: multiplied per 100 results

### Headwater Pricing

| Component | Cost |
|-----------|------|
| Software | **FREE** |
| Per Request | **$0** (software) |
| Proxy Data | ~$0.000045 per business (~$0.045/1K) |
| Infrastructure | ~$5-20/month (VPS) or free (local) |

#### Real-World Proxy Cost Example (Gladstone, OR Search)

Based on actual usage with residential proxy at **$3.50/GB**:

| Query Type | Response Size | Cost per Query |
|------------|---------------|----------------|
| Geocode (1 address) | ~187 bytes | $0.00000064 |
| Place Lookup | ~1.4 KB | $0.0000048 |
| Nearby Search (5 results) | ~8.2 KB | $0.000028 |
| Nearby Search (20 results) | ~14 KB | $0.000048 |
| Competitor Analysis | ~6 KB | $0.000021 |

**Actual Gladstone, OR Business Search (108 businesses):**

| Task | Queries | Data Used | Cost |
|------|---------|-----------|------|
| Geocode | 1 | 187 bytes | $0.0000006 |
| Food & Dining searches | 4 | ~56 KB | $0.00019 |
| Retail & Services | 6 | ~84 KB | $0.00029 |
| Professional Services | 4 | ~56 KB | $0.00019 |
| General businesses | 2 | ~28 KB | $0.00010 |
| **TOTAL** | **17 queries** | **~225 KB** | **$0.00077** |

**Cost per business found: $0.000007** (~108 businesses for less than 1/10th of a cent)

### Monthly Cost Examples (Including Proxy)

| Volume | DataForSEO (Standard) | DataForSEO (Live) | Headwater (with proxy) |
|--------|----------------------|-------------------|---------------------------|
| 1,000 | $0.60 | $2.00 | **$0.045** |
| 10,000 | $6.00 | $20.00 | **$0.45** |
| 100,000 | $60.00 | $200.00 | **$4.50** |
| 1,000,000 | $600.00 | $2,000.00 | **$45.00** |

**Headwater is 13-44x cheaper than DataForSEO even when including proxy costs.**

---

## Data Fields Comparison

### Basic Business Information

| Data Field | Headwater | DataForSEO Maps SERP |
|------------|:------------:|:--------------------:|
| Place ID / CID | Yes | Yes |
| Business Name | Yes | Yes |
| Full Address | Yes | Yes |
| Phone Number | Yes | Yes |
| Website URL | Yes | Yes |
| Google Maps URL | Yes | Yes |

### Location Data

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Latitude | Yes | Yes |
| Longitude | Yes | Yes |
| Plus Code | Yes | No |
| Geo-targeting (search) | Yes | Yes (granular) |

### Ratings & Reviews

| Data Field | Headwater | DataForSEO Maps | DataForSEO Reviews API* |
|------------|:------------:|:---------------:|:----------------------:|
| Average Rating | Yes | Yes | Yes |
| Review Count | Yes | Yes | Yes |
| Review Text (full) | Yes | No | Yes (separate API) |
| Star Breakdown (5/4/3/2/1) | Yes | No | Yes |
| Review Topics/Keywords | Yes | No | No |
| Topic Mention Counts | Yes | No | No |
| Sample Reviews | Yes | No | Yes |
| Reviewer Profile | Yes | No | Yes |
| Review Timestamp | Yes | No | Yes |
| Owner Responses | Yes | No | Yes |
| Review Photos | Yes | No | Yes |

*DataForSEO Reviews API requires separate requests and additional cost

### Business Hours & Timing

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Operating Hours | Yes | Yes |
| Hours by Day | Yes | Yes |
| Is Open Now | Yes | No |
| Popular Times (hourly) | Yes | No |
| Busy Percentages | Yes | No |
| Live Wait Times | **Yes** | No |
| Live Busyness Indicator | **Yes** | No |

### Service Options & Features

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Service Options (Dine-in, Delivery, etc.) | Yes | No |
| Accessibility Features | Yes | No |
| Amenities | Yes | No |
| Wi-Fi Available | Yes | No |
| Parking Info | Yes | No |

### Pricing Information

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Price Level ($-$$$$) | Yes | No |
| Price Per Person Range | Yes | No |

### Media & Content

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Photo URLs | Yes | No |
| High-res Photos | Yes | No |
| Menu Link | Yes | No |
| Order Online Link | Yes | No |
| Reservation Link | Yes | No |

### Related Data

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Related Places ("People also search for") | Yes | No |
| Category | Yes | Yes |
| Business Description | Yes | No |

### Contact Enrichment

| Data Field | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Email Extraction | Yes | No |
| Social Media Links | Yes | No |
| Facebook URL | Yes | No |
| Instagram URL | Yes | No |
| Twitter/X URL | Yes | No |

---

## API Endpoints Comparison

### Headwater Endpoints (30+)

| Endpoint | Description | DataForSEO Equivalent |
|----------|-------------|----------------------|
| `GET /search` | Search places by query | Google Maps SERP API |
| `POST /bulk-search` | Multiple queries at once | Batch tasks |
| `GET /place/{id}` | Get place details | No direct equivalent |
| `GET /place/{id}/reviews` | Paginated reviews | Reviews API (separate) |
| `GET /place/{id}/photos` | Get place photos | No |
| `GET /place/{id}/qa` | Q&A extraction | No |
| `GET /place/{id}/menu` | Menu extraction | No |
| `GET /place/{id}/analytics` | Review analytics | No |
| `GET /place/{id}/history` | Change history | No |
| `GET /place/{id}/attributes` | All attributes | No |
| `GET /place/{id}/availability` | Reservation check | No |
| `GET /place/{id}/streetview` | Street View URLs | No |
| `GET /nearby` | Coordinate-based search | Location targeting |
| `POST /competitors` | Competitor analysis | No |
| `POST /monitors` | Place monitoring | No |
| `POST /webhooks` | Job notifications | Postback URL |
| `GET /autocomplete` | Place suggestions | No |
| `POST /directions` | Route planning | No |
| `POST /geocode` | Address to coordinates | No |
| `GET /jobs/{id}/export` | CSV/Excel/JSON export | No native export |
| `POST /grid-search` | Grid-based area search | calculate_rectangles |
| `POST /bounding-box-search` | Bounding box search | No |
| `POST /location-search` | Search by location name | Location targeting |

### DataForSEO Endpoints

| Endpoint | Description |
|----------|-------------|
| Google Maps SERP API | Search results (up to 700/area) |
| Google Reviews API | Review data (separate pricing) |
| Business Data API | Additional business info |
| Locations API | Geo-targeting locations |

---

## Advanced Features Comparison

| Feature | Headwater | DataForSEO |
|---------|:------------:|:----------:|
| **Q&A Extraction** | Yes | No |
| **Menu Extraction** | Yes | No |
| **Competitor Analysis** | Yes | No |
| **Place Monitoring** | Yes | No |
| **Change Detection** | Yes | No |
| **Review Sentiment** | Yes | No |
| **Review Analytics** | Yes | No |
| **Async Job Queue** | Yes | Yes |
| **Webhooks** | Yes | Yes (postback) |
| **Export to CSV** | Yes | Manual |
| **Export to Excel** | Yes | Manual |
| **Export to JSON Lines** | Yes | Yes |
| **Caching (Redis)** | Yes | N/A (cloud) |
| **Rate Limiting** | Configurable | Fixed (2K/min) |

---

## Search Capabilities

| Capability | Headwater | DataForSEO |
|------------|:------------:|:----------:|
| Keyword Search | Yes | Yes |
| Location-based Search | Yes | Yes |
| Coordinate-based Search | Yes | Yes |
| Radius Search | Yes | Yes (search area) |
| Category Filter | Yes | Yes |
| Language Selection | Yes | Yes |
| Device Type (mobile/desktop) | Yes | Yes |
| Unlimited Results | Yes | Up to 700/area |
| Pagination | Yes | Depth parameter |
| Custom Zoom Level | Yes | No |
| Grid-based Search | **Yes** | Yes (calculate_rectangles) |
| Bounding Box Search | **Yes** | No |
| Location Name Search | **Yes** | Yes |

---

## Technical Comparison

| Aspect | Headwater | DataForSEO |
|--------|--------------|------------|
| **Architecture** | Self-hosted Docker | Cloud SaaS |
| **Language** | Python/FastAPI | REST API |
| **Browser Engine** | Playwright | Proprietary |
| **Database** | PostgreSQL + Redis | N/A |
| **Authentication** | API Key | API Key |
| **Documentation** | OpenAPI/Swagger | Online docs |
| **SDK** | Python native | Multiple SDKs |
| **Uptime SLA** | Self-managed | 99.9% |
| **Support** | Community/Self | 24/7 Support |

---

## Response Time Comparison

| Scenario | Headwater | DataForSEO |
|----------|--------------|------------|
| Single Place Search | 2-5 sec | 6 sec (Live) |
| 10 Places | 5-15 sec | 6 sec (Live) |
| 100 Places | 30-60 sec | ~45 min (Standard) |
| With Reviews | +3-5 sec/place | Separate API call |
| With Email Extraction | +5-10 sec/place | Not available |

---

## Use Case Recommendations

### Choose Headwater If:

1. **Cost is a priority** - Zero per-request costs
2. **You need advanced features** like:
   - Q&A extraction
   - Menu data
   - Competitor analysis
   - Place monitoring
   - Review topics/keywords
3. **You want full data ownership**
4. **You need unlimited usage**
5. **You want to customize the scraper**
6. **Privacy/compliance requires self-hosting**

### Choose DataForSEO If:

1. **You need enterprise reliability** with SLA
2. **You want zero infrastructure management**
3. **You need granular geo-targeting** (their specialty)
4. **You're building SEO rank tracking tools**
5. **You need 24/7 support**
6. **Budget allows $0.60-$2 per 1K requests**
7. **You only need basic listing data** (not reviews/photos/menus)

---

## Feature Summary Matrix

| Category | Headwater | DataForSEO |
|----------|:------------:|:----------:|
| **Basic Listing Data** | Full | Full |
| **Reviews (text)** | Yes | Separate API |
| **Review Analytics** | Yes | No |
| **Photos** | Yes | No |
| **Popular Times** | Yes | No |
| **Q&A** | Yes | No |
| **Menus** | Yes | No |
| **Email Extraction** | Yes | No |
| **Social Media** | Yes | No |
| **Competitor Analysis** | Yes | No |
| **Place Monitoring** | Yes | No |
| **Geo-targeting** | **Excellent** | Excellent |
| **Grid-based Search** | **Yes** | Yes |
| **Live Wait Times** | **Yes** | No |
| **Self-hosted** | Yes | No |
| **Cost per 1K** | **$0.045** | $0.60-$2.00 |
| **Cost Advantage** | **13-44x cheaper** | - |

---

## Conclusion

**Headwater provides significantly more data points and features** than DataForSEO's Google Maps API, including:

- 40+ data fields vs ~10 for basic DataForSEO Maps SERP
- Review topics, popular times, Q&A, menus (not available in DataForSEO)
- Email and social media extraction (not available in DataForSEO)
- Competitor analysis and place monitoring (unique features)
- **Live wait times and busyness indicators** (unique feature)
- **Grid-based search** matching DataForSEO's calculate_rectangles feature
- **Bounding box search** for custom area mapping

### Cost Summary (with Real Proxy Data)

| Metric | Headwater | DataForSEO |
|--------|--------------|------------|
| Cost per 1K businesses | **$0.045** | $0.60 - $2.00 |
| 100K businesses/month | **$4.50** | $60 - $200 |
| 1M businesses/month | **$45** | $600 - $2,000 |
| **Savings** | **13-44x cheaper** | Baseline |

*Based on actual Gladstone, OR search: 108 businesses scraped for $0.00077 total*

**DataForSEO advantages:**
- No infrastructure to manage
- Industry-leading geo-targeting precision
- Enterprise SLA and support
- Better for pure SEO rank tracking use cases

**For most data extraction use cases**, Headwater offers better value due to:
1. **13-44x lower cost** even including proxy expenses
2. **3-4x more data fields** extracted per business
3. Advanced analytical features (Q&A, menus, monitoring)
4. Full control over data and infrastructure

---

## Sources

- [DataForSEO Google Maps API](https://dataforseo.com/apis/serp-api/google-maps-api)
- [DataForSEO Pricing](https://dataforseo.com/pricing/serp/google-maps-serp-api)
- [DataForSEO Reviews API](https://dataforseo.com/apis/reviews-api/google-reviews-api)
- [DataForSEO Documentation](https://docs.dataforseo.com/v3/serp/google/maps/overview/)
