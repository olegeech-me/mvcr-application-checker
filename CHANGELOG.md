# CHANGELOG

## [v1.0.3] - 2024-11-17
### Added
- Implemented lazy polling logic for `NOT_FOUND` applications
- Enhanced monitoring and expiration checks for applications not immediately found
- Improved logging and notification systems for application updates

## [v1.0.2] - 2024-11-15
### Changed
- Added support to keep the application state category stored in the database for more robust state tracking

## [v1.0.1] - 2024-11-12
### Added
- Exposed bot and fetcher version details to enhance transparency and tracking across deployments

## [v1.0.0] - 2024-11-09
### Changed
- Filtered out unsupported HTML tags to ensure proper text formatting
- Updated fetcher to adapt to layout changes on the external website
- Replaced `ALLOWED_YEARS` with a more dynamic `get_allowed_years()` function for improved flexibility

---

### Initial Development Phase (Pre-v1.0.0)
- Added core functionality for status updates and monitoring
- Integrated foundational database management and message queue handling
- Established a basic bot-fetcher workflow to process and track applications
- Added basic testing
- Language translations