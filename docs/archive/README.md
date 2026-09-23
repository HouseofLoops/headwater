# Archive

Point-in-time documents: logs, reviews, test reports and comparisons that describe the project
as it was on a given date. They are kept for the reasoning and history they record, not as a
description of the current code. File names, endpoints, versions and tooling mentioned here may
no longer exist.

For current behaviour, read the living docs one level up (`docs/`), the source, or `/api/docs`
on a running instance. Open follow-up work is tracked in [ROADMAP.md](../ROADMAP.md#open-items).

| Document | What it is | Date |
|----------|------------|------|
| [CODE_FIXES_LOG.md](CODE_FIXES_LOG.md) | Log of 12 fixes (rate limiter, cache manager, gather timeouts, dependency injection, ...), all marked complete | 2025-12-18 |
| [IMPROVEMENT_RECOMMENDATIONS.md](IMPROVEMENT_RECOMMENDATIONS.md) | Improvement ideas and complementary libraries; still-open items moved to the roadmap | undated |
| [testing/ENDPOINT_TESTING.md](testing/ENDPOINT_TESTING.md) | Endpoint test run against API 1.5.3 | 2025-12-27 |
| [testing/PERFORMANCE_REVIEW.md](testing/PERFORMANCE_REVIEW.md) | Performance and consolidation review of API 1.5.3 | 2025-12-27 |
| [features/FEATURE_IMPROVEMENTS_GEO_WAIT_TIMES.md](features/FEATURE_IMPROVEMENTS_GEO_WAIT_TIMES.md) | Write-up of the geo-targeting and live wait-times work (implemented) | 2025-12-28 |
| [comparisons/google_maps_service_comparison.md](comparisons/google_maps_service_comparison.md) | Google Maps extraction compared with commercial and open-source alternatives | 2025-12-28 |
| [comparisons/headwater_vs_dataforseo_comparison.md](comparisons/headwater_vs_dataforseo_comparison.md) | Feature comparison with DataForSEO | 2025-12-28 |

## Adding to the archive

Move a document here with `git mv` (so its history follows it) once it stops describing the
current state of the project, for example a dated report or a review whose actions are done.
Do not edit an archived document to match today's code; add a short dated note at the top instead.
