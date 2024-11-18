import logging
from datetime import datetime, timedelta
from collections import Counter
from bot.loader import STATISTICS_PERIOD_DAYS
from bot.utils import generate_oam_full_string

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
        return start_date, end_date

    async def get_general_stats(self, period_days=None):
        """Gather general statistics for each application state"""
        start_date, end_date = self._get_period_dates(period_days)
        applications = await self.db.fetch_application_states_within_period(start_date, end_date)

        total_applications = len(applications)
        status_counts = Counter(app['application_state'] for app in applications)

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
            return None, {}

        total_time = sum(app['processing_time'] for app in processing_times)
        overall_average = total_time / len(processing_times)

        times_by_category = {}
        for app in processing_times:
            app_type = app['application_type']
            # use setdefault to make sure key exists and initialized
            times_by_category.setdefault(app_type, []).append(app['processing_time'])

        average_times_by_category = {
            app_type: sum(times) / len(times)
            for app_type, times in times_by_category.items()
        }

        return overall_average, average_times_by_category

    async def get_common_update_time(self, period_days=None):
        """Get most common hour when MVCR uploads new results"""
        start_date, end_date = self._get_period_dates(period_days)
        hours = await self.db.fetch_status_change_hours_within_period(start_date, end_date)
        if not hours:
            return None

        hour_counts = Counter(hours)
        most_common_hour, _ = hour_counts.most_common(1)[0]
        return int(most_common_hour)

    async def predict_user_application_time(self, chat_id, period_days=None):
        """Predict the approval time for the user's applications"""
        applications = await self.db.fetch_user_subscriptions(chat_id)

        # Get average processing times by category
        _overall_avg, avg_times_by_category = await self.calculate_average_processing_times(period_days)

        predictions = []
        for app in applications:
            if app['is_resolved']:
                continue  # Skip resolved

            type = app['application_type']
            avg_time = avg_times_by_category.get(type)
            if not avg_time:
                continue  # Skip if no average time is available for this type

            time_elapsed = (datetime.utcnow() - app['created_at']).total_seconds()
            estimated_remaining = avg_time - time_elapsed
            if estimated_remaining > 0:
                predictions.append({
                    'application_number': generate_oam_full_string(app),
                    'days_remaining': estimated_remaining / 86400  # Convert seconds to days
                })
        return predictions