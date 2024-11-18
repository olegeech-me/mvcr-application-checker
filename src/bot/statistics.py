import logging
from datetime import datetime, timedelta
from collections import Counter
from bot.loader import STATISTICS_PERIOD_DAYS, STATISTICS_MIN_TRESHOLD_DAYS
from bot.utils import generate_oam_full_string

MIN_PROCESSING_TIME_SECONDS = STATISTICS_MIN_TRESHOLD_DAYS * 86400

logger = logging.getLogger(__name__)


class Statistics:
    def __init__(self, db):
        self.db = db

    def _get_period_dates(self, period_days):
        """Helper method to calculate start and end dates based on period_days"""
        if period_days is None:
            period_days = STATISTICS_PERIOD_DAYS
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=period_days)
        logger.debug(f"Period dates calculated: start_date={start_date}, end_date={end_date}")
        return start_date, end_date

    async def get_general_stats(self, period_days=None):
        """Gather general statistics for each application state"""
        start_date, end_date = self._get_period_dates(period_days)
        applications = await self.db.fetch_application_states_within_period(start_date, end_date)
        logger.debug(f"Fetched {len(applications)} applications within period")

        total_applications = len(applications)
        status_counts = Counter(app['application_state'] for app in applications)
        logger.debug(f"Application status counts: {status_counts}")

        return {
            'total_applications': total_applications,
            'approved': status_counts.get('APPROVED', 0),
            'denied': status_counts.get('DENIED', 0),
            'in_progress': status_counts.get('IN_PROGRESS', 0),
            'not_found': status_counts.get('NOT_FOUND', 0),
        }

    async def calculate_average_processing_times(self, period_days=None):
        """Get average processing time, overall and categorized by application type"""
        start_date, end_date = self._get_period_dates(period_days)
        processing_times = await self.db.fetch_processing_times_within_period(start_date, end_date)
        if not processing_times:
            logger.info("No processing times available")
            return None, {}

        logger.debug(f"Processing times fetched: {processing_times}")

        # Filter out processing times below the minimum threshold
        filtered_processing_times = [
            app for app in processing_times if app['processing_time'] >= MIN_PROCESSING_TIME_SECONDS
        ]
        if not filtered_processing_times:
            logger.info("No processing times above the minimum threshold")
            return None, {}

        total_time = sum(app['processing_time'] for app in processing_times)
        overall_average = total_time / len(processing_times)
        logger.debug(f"Overall average processing time: {overall_average} seconds")

        times_by_category = {}
        for app in processing_times:
            app_type = app['application_type']
            # use setdefault to make sure key exists and initialized
            times_by_category.setdefault(app_type, []).append(app['processing_time'])

        average_times_by_category = {
            app_type: sum(times) / len(times)
            for app_type, times in times_by_category.items()
        }
        logger.debug(f"Average processing times by category: {average_times_by_category}")
        return overall_average, average_times_by_category

    async def get_common_update_time(self, period_days=None):
        """Get most common hour when MVCR uploads new results"""
        start_date, end_date = self._get_period_dates(period_days)
        hours = await self.db.fetch_status_change_hours_within_period(start_date, end_date)
        if not hours:
            logger.info("No status change hours available")
            return None

        logger.debug(f"Status change hours fetched: {hours}")
        hour_counts = Counter(hours)
        most_common_hour, _ = hour_counts.most_common(1)[0]
        logger.debug(f"Most common update hour: {most_common_hour}")
        return int(most_common_hour)

    async def predict_user_application_time(self, chat_id, period_days=None):
        """Predict the approval time for the user's applications"""
        applications = await self.db.fetch_user_subscriptions(chat_id)
        logger.debug(f"User {chat_id} has {len(applications)} applications: {applications}")

        # Get average processing times by category
        _overall_avg, avg_times_by_category = await self.calculate_average_processing_times(period_days)

        predictions = []
        for app in applications:
            if app['is_resolved']:
                continue  # Skip resolved

            type = app['application_type']
            avg_time = avg_times_by_category.get(type)
            if not avg_time:
                logger.info(f"No average time available for application type {type}")
                continue  # Skip if no average time is available for this type

            logger.info(f"Application {app['application_id']} type: {type}, avg_time: {avg_time}")
            time_elapsed = (datetime.utcnow() - app['created_at']).total_seconds()

            estimated_remaining = float(avg_time) - time_elapsed
            logger.info(
                f"Application {app['application_id']} time_elapsed: {time_elapsed} seconds, "
                f"estimated_remaining: {estimated_remaining} seconds remaining"
            )
            if estimated_remaining > 0:
                predictions.append({
                    'application_number': generate_oam_full_string(app),
                    'days_remaining': estimated_remaining / 86400  # Convert seconds to days
                })

        return predictions