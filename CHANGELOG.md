# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-29

### Added

- Twelve FastMCP tools for current readings, station discovery, historical
  summaries, trends, city comparisons, rankings, health guidance, threshold
  checks, and nearest-station lookup.
- Turkish-aware province, district, and station resolution with structured
  ambiguity and no-match responses.
- Keyless OpenStreetMap Nominatim geocoding with caching, single-flight
  requests, and self-throttling.
- UHKİA bulk caching with stale-if-error support, request single-flight,
  retry with exponential backoff and jitter, and a self-imposed request rate
  limit.
- Fixture- and mock-based test coverage that does not call live external
  services.
- Ruff linting and formatting plus GitHub Actions CI for pushes and pull
  requests targeting `main`.
- Installation, configuration, tool examples, data-source attribution,
  privacy notes, and an MIT license.
