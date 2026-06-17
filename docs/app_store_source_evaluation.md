# App Store Review Source Evaluation

## Purpose

John accepted the Amazon conclusion and asked us not to choose Steam only because it is technically clean. This evaluation treats Steam as an ingestion mechanics testbed while we compare Google Play and Apple App Store reviews as a more commercially mainstream source.

The current requirement is public third-party app review data. That means source paths that only work for apps we own or administer are documented as useful fallbacks, not as the v1 primary source.

## Initial Findings

- Google Play's official review API is structured JSON and includes review IDs, text, timestamps, ratings, language, app version, device metadata, helpful votes, developer replies, token pagination, and translation support. It requires OAuth/service-account authorization for the developer's app, returns only reviews with comments, and is documented around recent review retrieval. This is strong for owned or partner apps, but it does not satisfy public third-party collection.
- Apple's App Store Connect customer review API is also structured and includes rating, title, body, reviewer nickname, created date, territory, and response fields. It requires App Store Connect JWT credentials and is framed around reviews for apps in that account, so it also does not satisfy public third-party collection.
- Public Google Play and Apple storefront pages are commercially relevant and no-login, but no documented public endpoint was found that provides complete paginated full-review rows with stable IDs and an incremental cursor suitable for production.
- Licensed app-intelligence providers are the strongest current path if the project needs broad public third-party app coverage. Appfigures, AppFollow, AppTweak, and Sensor Tower all expose API or feed products that should be trialed before we build a production app-store ingestion package.

## Feasibility Matrix

The working matrix lives at `data/evaluation/app_store_source_matrix.csv`.

Current interpretation:

- **Official owner APIs:** technically strong, commercially relevant, but fail the public-third-party requirement.
- **Public storefront pages:** useful only for conservative no-login smoke tests; not production-ready without clearer legal/product access and stable pagination.
- **Licensed providers:** best candidate class for v1 if budget and usage terms work.

## Public Target Set

The target set lives at `data/targets/app_store_public_apps.csv`. It contains 20 mainstream cross-platform apps across shopping, travel, food delivery, finance, entertainment, social, AI/tools, education, and health.

The target list is for source evaluation only. It does not mutate production ingestion targets and does not imply permission to collect full public reviews from undocumented endpoints.

## Smoke Test

Run the conservative storefront smoke test:

```bash
python app_store_evaluate.py smoke \
  --targets data/targets/app_store_public_apps.csv \
  --limit 3 \
  --output /tmp/app_store_storefront_smoke.json
```

The smoke test fetches only public app detail pages. It does not call hidden review endpoints, use login state, store cookies, solve CAPTCHAs, rotate proxies, or attempt anti-bot bypasses. The output is evidence about page accessibility and visible review/rating markers, not proof of full-review production viability.

Summarize the target set:

```bash
python app_store_evaluate.py targets --targets data/targets/app_store_public_apps.csv
```

## Decision Gate

A source should pass only if it supports:

- Public third-party apps, not only apps we own.
- Full written review text, rating, date, app identity, platform, country or locale where available, and stable review identity or a reliable dedupe key.
- Enough depth for downstream analytics: at least thousands of reviews for popular apps, not only top-visible snippets.
- Daily incremental refresh without repeatedly re-ingesting full history.
- Clean operation: no login bypass, personal cookies, CAPTCHA solving, residential proxies, hidden endpoint dependency, or unclear terms.
- A practical Postgres-backed production path.

If no public path passes cleanly, the recommendation should be a licensed provider trial, official APIs for owned or partner apps only, or a different commercially relevant source.

## References

- Google Play Developer API review list: https://developers.google.com/android-publisher/api-ref/rest/v3/reviews/list
- Google Play Reply to Reviews API: https://developers.google.com/android-publisher/reply-to-reviews
- Google Play review resource fields: https://developers.google.com/android-publisher/api-ref/rest/v3/reviews
- Apple App Store Connect customer reviews: https://developer.apple.com/documentation/appstoreconnectapi/customer-reviews
- Apple customer review fields: https://developer.apple.com/documentation/appstoreconnectapi/customerreview/attributes-data.dictionary
- Apple App Store Connect API auth: https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api
- Appfigures reviews API: https://docs.appfigures.com/api/reference/v2/reviews
- AppFollow reviews API: https://docs.api.appfollow.io/reference/reviews_api_v2_reviews_get-1
- AppTweak app reviews API: https://developers.apptweak.com/reference/app-reviews
- Sensor Tower Connect: https://sensortower.com/product/connect
