# 🏠 immoscout

[![PyPI](https://img.shields.io/pypi/v/immoscout?style=flat-square)](https://pypi.org/project/immoscout/)
[![CI](https://github.com/wiestju/ImmoScout/actions/workflows/ci.yml/badge.svg)](https://github.com/wiestju/ImmoScout/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/immoscout?style=flat-square)](https://pypi.org/project/immoscout/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

**A typed, unofficial Python client for [ImmobilienScout24](https://www.immobilienscout24.de)** —
search real-estate listings, fetch expose details, and plug it all into AI agents.

Instead of a raw JSON blob, you get clean typed objects (`Listing`, `Expose`) — and a
built-in **[MCP](https://modelcontextprotocol.io) server** so assistants like Claude can
search real estate for you natively.

📖 **[Documentation](https://wiestju.github.io/ImmoScout/)** · `pip install immoscout`

> ⚠️ Unofficial and not affiliated with ImmobilienScout24. For personal use, and gentle
> with request volume — see the [DISCLAIMER](DISCLAIMER.md).

## 🍴 About this fork

Fork of [wiestju/immoscout](https://github.com/wiestju/immoscout) that adds **applying to
listings** from the MCP server, plus a compatibility fix.

**New MCP tools**

- **`apply_to_listing(expose_id, message?, fields?, confirm=false)`** — fills the listing's
  contact form ("Nachricht") in a real, logged-in Chrome window via Playwright.
  - Without `confirm` it only fills the form and returns the values, still-empty fields and a
    screenshot — **nothing is sent**. With `confirm=true` it clicks "Abschicken" and checks
    for ImmoScout's "Nachricht gesendet" confirmation.
  - Handles the three contact-form variants ImmoScout uses (short applicant form with move-in
    date / income bracket, minimal form, extended form with adults/kids, exact income,
    rent arrears, smoker, …). Defaults come from an applicant profile (see below); per-call
    `fields` override them, and fields a form doesn't have are skipped and reported.
  - Never contacts a listing twice; daily cap (`IMMOSCOUT_MAX_APPLICATIONS_PER_DAY`, default
    20) and a minimum gap between sends (`IMMOSCOUT_MIN_SECONDS_BETWEEN_APPLICATIONS`, 60 s).
  - Refuses to send when logged out or when a captcha appears; switches off the
    "moving company offers" opt-in.
- **`list_applications()`** — what has been sent so far.

**Setup**

```bash
pip install -e ".[mcp,apply]"
playwright install chromium   # Chrome itself is used by default (IMMOSCOUT_BROWSER_CHANNEL)
immoscout-login               # opens Chrome: log in, then close the window
```

Log in with ImmoScout **e-mail + password** — a "Mit Google anmelden" session lives only as
long as the browser window and is gone on the next run.

The applicant profile is a JSON file passed via `IMMOSCOUT_PROFILE` (default:
`%LOCALAPPDATA%/immoscout/profile.json`):

```json
{
  "message": "Sehr geehrte/r Vermieter/in, …",
  "fields": {
    "salutation": "MALE", "firstName": "…", "lastName": "…", "phoneNumber": "…",
    "street": "…", "houseNumber": "…", "postcode": "…", "city": "…",
    "moveInDateType": "CONCRETE", "moveInDate": "01.12.2026",
    "numberOfPersons": "ONE_PERSON", "numberOfAdults": "1", "numberOfKids": "0",
    "hasPets": "FALSE", "smoker": "FALSE",
    "employmentRelationship": "PUBLIC_EMPLOYEE", "employmentStatus": "INDEFINITE_PERIOD",
    "income": "OVER_3000_UPTO_4000", "incomeAmount": "3500",
    "applicationPackageCompleted": "TRUE", "forCommercialPurposes": "FALSE",
    "rentArrears": "FALSE", "insolvencyProcess": "FALSE", "depositGuarantee": "FALSE"
  }
}
```

Register with Claude Code:

```bash
claude mcp add immoscout -e IMMOSCOUT_PROFILE=/path/to/profile.json -- immoscout-mcp
```

Browser profile, application log, screenshots and captured submit requests are stored in
`%LOCALAPPDATA%/immoscout/` (override with `IMMOSCOUT_DATA_DIR`).

**Fix:** pins `mcp<2` — mcp 2.x renamed `FastMCP`, so a fresh `pip install "immoscout[mcp]"`
currently fails to start the server.

> Automating contact requests may conflict with ImmobilienScout24's terms of use. Review
> each message before confirming, and keep the volume low.

## ✨ Features

- 🚫 **No captchas to solve** — talks to ImmobilienScout24's **mobile API**, which returns
  clean JSON directly. No headless browser, no captcha-solving, no HTML scraping — where the
  website makes even human users pass captchas, the mobile endpoints just answer.
- 🔍 **Typed results** — `search()` returns `SearchResult` with parsed `Listing` objects
  (price, rooms, m², address, URL) — not a nested dict you have to reverse-engineer.
- 🎯 **Validated filters** — `SearchFilter` with price/rooms/space ranges and real-estate types.
- 🗺️ **Place names, not codes** — `region="München"` is auto-resolved to the right geocode path.
- 📄 **Auto-pagination & counts** — `search_all()` walks every page; `count()` returns totals only.
- 🛡️ **Robust HTTP** — request timeouts, retries with backoff, and explicit rate-limit errors.
- 🤖 **Agent-native** — an MCP server (`immoscout-mcp`) exposes search as tools for Claude & co.
- ⌨️ **CLI** — `immoscout search …` and `immoscout expose …`.
- 🧩 **Nothing hidden** — every object keeps the untouched API payload on `.raw`.

## 🚀 Quick Start

```python
from immoscout import ImmoscoutClient

client = ImmoscoutClient()

result = client.search(region="Berlin", price_max=1200, rooms_min=2)  # place name auto-resolved
print(f"{result.total_results} results")

for listing in result.listings[:5]:
    print(listing.title)
    print(f"  {listing.price:.0f} {listing.currency} · {listing.rooms:g} rooms · {listing.living_space:g} m²")
    print(f"  {listing.address.line}")
    print(f"  {listing.url}")
```

### Finding a region

You can pass a **place name** directly (`region="München"`) and it's resolved for you. To
see the options, or to store the exact path, look them up:

```python
client.suggest_regions("münchen")[0].region   # -> "/de/bayern/muenchen"
```

```bash
immoscout regions münchen
# /de/bayern/muenchen          city       München
# /de/bayern/muenchen-kreis    district   München (Kreis)
```

## 🎯 Filters & pagination

```python
from immoscout import ImmoscoutClient, RealEstateType, SearchFilter

client = ImmoscoutClient()

query = SearchFilter(
    region="/de/bayern/muenchen",
    real_estate_type=RealEstateType.APARTMENT_RENT,
    price_max=1500,
    rooms_min=2,
    living_space_min=50,
)

# Walk every result page (one request per page) — cap it to stay gentle:
for listing in client.search_all(query, max_pages=3):
    print(listing.price, listing.title)
```

## 📄 Expose details

```python
expose = client.get_expose("169446368")
print(expose.title, expose.price, expose.rooms, expose.living_space)
print(expose.address)
print(expose.description)
print(expose.attributes)   # {"Wohnungstyp": "Etagenwohnung", "Etage": "2 von 4", ...}
```

## ⌨️ CLI

```bash
immoscout regions münchen                                   # find a region path
immoscout search --region /de/berlin/berlin --price-max 1200 --rooms-min 2
immoscout search --region /de/berlin/berlin --json          # machine-readable
immoscout expose 169446368
```

## 🤖 Use it from AI agents (MCP)

`immoscout` ships a [Model Context Protocol](https://modelcontextprotocol.io) server, so
any MCP client (Claude Desktop, Claude Code, Cursor, …) can search real estate natively.

```bash
pip install "immoscout[mcp]"
immoscout-mcp        # runs the server (stdio)
```

Register it in your MCP client — e.g. Claude Desktop's `mcpServers`:

```json
{
  "mcpServers": {
    "immoscout": { "command": "immoscout-mcp" }
  }
}
```

The agent then has these tools:

- **`search_listings`** — region, real-estate type, price/rooms/space bounds, max results.
- **`count_listings`** — how many match, without fetching them.
- **`get_expose`** — full details for a listing ID.
- **`suggest_regions`** — resolve a place name to region paths (so the agent can search any city).

Now you can ask *"find me 2-room apartments in Berlin under 1200 € and summarize the three
cheapest"* and the agent does the lookup itself.

## 🧱 API overview

| Object | What it is |
| --- | --- |
| `ImmoscoutClient(timeout, max_retries, backoff)` | HTTP client — `search()`, `search_all()`, `count()`, `get_expose()`, `suggest_regions()` |
| `SearchFilter(...)` | Validated query; `.to_params()` compiles to API params |
| `RealEstateType` | Enum: apartment/house rent & buy, flat-share room, garage |
| `SearchResult` | `total_results`, `number_of_pages`, `listings`, `raw` |
| `Listing` | Parsed result: price, rooms, `living_space`, `address`, `url`, `raw` |
| `Expose` | Detail view: price, `attributes`, `description`, `raw` |
| `ImmoscoutError` | Base; `RequestError`, `NotFoundError`, `RateLimitError` |

## 🛠️ Development

```bash
pip install -e ".[dev]"
ruff check .
pytest              # unit tests are fully mocked — no network needed
```

CI runs ruff + pytest across Python 3.10–3.13.

## ⚖️ Responsible use

ImmobilienScout24 actively rate-limits and blocks automated traffic. Keep the built-in
timeouts/retries, add delays, and don't scrape at scale. This project is for personal and
educational use — see the [DISCLAIMER](DISCLAIMER.md).

## 📄 License

[MIT](LICENSE)
