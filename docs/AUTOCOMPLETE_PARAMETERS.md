# Google Autocomplete API Parameters Reference

This document tracks all parameters implemented in the Headwater Google Autocomplete API endpoint.

## Endpoint
`GET /api/v1/google-autocomplete/autocomplete`

---

## Parameter Summary

| Status | Category | Count |
|--------|----------|-------|
| Implemented | Core Parameters | 3 |
| Implemented | Geographic & Language | 4 |
| Implemented | Data Source | 1 |
| Implemented | Search Enhancement | 4 |
| Implemented | Response Format | 2 |
| Implemented | Advanced/Personalization | 6 |
| Implemented | Endpoint-Specific | 1 |
| **NEW** | Safety & Content Filtering | 3 |
| **NEW** | Encoding | 2 |
| **NEW** | Personalization Controls | 2 |
| **NEW** | Browser/Client Analytics | 4 |
| **Total** | | **32** |

---

## Core Parameters

| Parameter | Type | Default | Required | Description |
|-----------|------|---------|----------|-------------|
| `q` | `string` | - | Yes | Search query string (URL encoded). Cannot be empty. |
| `output` | `enum` | `toolbar` | No | Response format |
| `client` | `enum` | `None` | No | Client identifier |

### Output Format Values
| Value | Description |
|-------|-------------|
| `toolbar` | XML format used by Google Toolbar |
| `chrome` | JSON format used by Chrome browser |
| `firefox` | JSON format used by Firefox browser |
| `xml` | Standard XML format (identical to toolbar) |
| `safari` | JSON format used by Safari browser |
| `opera` | JSON format used by Opera browser |

### Client Type Values
| Value | Description |
|-------|-------------|
| `chrome` | Google Chrome browser |
| `firefox` | Mozilla Firefox browser |
| `safari` | Apple Safari browser |
| `opera` | Opera browser |

---

## Geographic & Language Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gl` | `string` | `US` | Geographic location using ISO country codes (e.g., US, GB, DE) |
| `hl` | `string` | `en` | Host language using ISO language codes (e.g., en, es, fr) |
| `cr` | `string` | `None` | Country restrict (e.g., countryUS, countryGB) |
| `lr` | `string` | `None` | **NEW** Language restriction (e.g., lang_en, lang_es) |

---

## Data Source Parameter

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ds` | `enum` | `None` | Data source for suggestions |

### Data Source Values
| Value | Description |
|-------|-------------|
| *(empty)* | General web search (default) |
| `yt` | YouTube video suggestions |
| `i` | Image search suggestions |
| `n` | News search suggestions |
| `s` | Shopping/product suggestions |
| `v` | Video search suggestions |
| `b` | Book search suggestions |
| `p` | Patent search suggestions |
| `fin` | Financial/stock suggestions |
| `recipe` | Recipe suggestions |
| `scholar` | Google Scholar academic suggestions |
| `play` | Google Play Store suggestions |
| `maps` | Google Maps location suggestions |
| `flights` | Google Flights suggestions |
| `hotels` | Google Hotels suggestions |

---

## Search Enhancement Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `spell` | `int` | `1` | Enable spell correction (0=disabled, 1=enabled) |
| `cp` | `int` | `None` | Cursor position in query (character position) |
| `gs_rn` | `int` | `None` | Request number for sequential numbering |
| `gs_id` | `string` | `None` | Session ID for tracking |

---

## Response Format Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `callback` | `string` | `None` | JSONP callback function name |
| `jsonp` | `string` | `None` | JSONP wrapper (alternative to callback) |

---

## Advanced/Personalization Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `psi` | `int` | `None` | Personalized search (0=disabled, 1=enabled) |
| `pq` | `string` | `None` | Previous query for query refinement |
| `complete` | `int` | `None` | Completion type affecting suggestion logic |
| `suggid` | `string` | `None` | Suggestion ID for internal tracking |
| `gs_l` | `string` | `None` | Google search location codes |

---

## Safety & Content Filtering Parameters (NEW)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `safe` | `enum` | `None` | SafeSearch content filtering |
| `nfpr` | `int` | `None` | Disable auto-correction (0=enable, 1=disable) |
| `filter` | `int` | `None` | Duplicate/similar results filtering (0=show all, 1=filter) |

### Safe Search Values
| Value | Description |
|-------|-------------|
| `active` | Filter explicit content |
| `off` | Show all content (no filtering) |

---

## Encoding Parameters (NEW)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ie` | `string` | `UTF-8` | Input encoding |
| `oe` | `string` | `UTF-8` | Output encoding |

### Supported Encodings
- `UTF-8` (recommended)
- `ISO-8859-1`
- `windows-1252`

---

## Personalization Control Parameters (NEW)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pws` | `int` | `None` | Personalized Web Search toggle (0=disable, 1=enable) |
| `authuser` | `int` | `None` | Select Google account for multi-login (0, 1, 2, etc.) |

---

## Browser/Client Analytics Parameters (NEW)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `oq` | `string` | `None` | Original query (text typed before using suggestion) |
| `sclient` | `string` | `None` | Search client identifier |
| `aqs` | `string` | `None` | Assisted Query Stats (Chrome analytics format) |
| `xssi` | `string` | `None` | XSSI protection toggle (t=enabled, f=disabled) |

### Search Client Values
| Value | Description |
|-------|-------------|
| `gws-wiz` | Google Homepage |
| `gws-wiz-local` | Google Local searches |
| `psy-ab` | Chrome on Google.com |

---

## Endpoint-Specific Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `variations` | `bool` | `False` | Return comprehensive keyword variations instead of raw suggestions |

---

## Example Requests

### Basic Request
```bash
curl "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=python" \
  -H "X-API-Key: your-api-key"
```

### With Safety Filtering
```bash
curl "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=python&safe=active&gl=US&hl=en" \
  -H "X-API-Key: your-api-key"
```

### YouTube Suggestions with Language Restriction
```bash
curl "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=music&ds=yt&lr=lang_en" \
  -H "X-API-Key: your-api-key"
```

### Disable Personalization
```bash
curl "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=news&pws=0&nfpr=1" \
  -H "X-API-Key: your-api-key"
```

### Full Parameter Example
```bash
curl "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=test&output=chrome&client=chrome&gl=US&hl=en&safe=active&ie=UTF-8&oe=UTF-8&pws=0" \
  -H "X-API-Key: your-api-key"
```

---

## Implementation Status

### Completed
- [x] Core parameters (q, output, client)
- [x] Geographic & Language (gl, hl, cr)
- [x] Data Source (ds with 15 values)
- [x] Search Enhancement (spell, cp, gs_rn, gs_id)
- [x] Response Format (callback, jsonp)
- [x] Advanced (psi, pq, complete, suggid, gs_l)
- [x] Endpoint-specific (variations)
- [x] Safety & Content Filtering (safe, nfpr, filter)
- [x] Encoding (ie, oe)
- [x] Personalization Controls (pws, authuser)
- [x] Language Restriction (lr)
- [x] Browser/Client Analytics (oq, sclient, aqs, xssi)

### Total: 32 Parameters

---

## Changelog

### v1.6.0 (2025-12-19)
- Added 13 new parameters:
  - Safety: `safe`, `nfpr`, `filter`
  - Encoding: `ie`, `oe`
  - Personalization: `pws`, `authuser`
  - Language: `lr`
  - Analytics: `oq`, `sclient`, `aqs`, `xssi`
- Added XSSI prefix stripping (`)]}'`) for responses when `xssi=t` is used
- Added support for Google's internal callback format (`window.google.ac.h(...)`) triggered by `sclient` parameter
- All 32 parameters tested and verified working

### v1.5.0
- Initial implementation with 19 parameters
- Added empty query validation
