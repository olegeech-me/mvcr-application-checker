import asyncpg
import datetime
import logging
import pytz
import asyncio
from bot.texts import message_texts
from bot.utils import categorize_application_status

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 2  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dbname, user, password, host, port, loop):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.loop = loop
        self.pool = None

    async def connect(self, max_retries=MAX_RETRIES, delay=RETRY_DELAY):
        """Connect to the database with retries"""
        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    database=self.dbname,
                    user=self.user,
                    password=self.password,
                    host=self.host,
                    port=self.port,
                    min_size=5,
                    max_size=20,
                )
                logger.info("Connected to the DB")
                break
            except Exception as e:
                logger.error(f"Failed to connect to the database. Attempt {attempt}/{max_retries}. Error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2  # Double the delay for next retry
                else:
                    logger.error("Max retries reached. Unable to connect to the database")
                    raise

    async def insert_user(self, chat_id, first_name, username=None, last_name=None, lang="EN"):
        """Insert a new user to the Users table"""

        logger.info(f"Adding user with chatID {chat_id} to DB")
        query = "INSERT INTO Users " "(chat_id, username, first_name, last_name, language) " "VALUES ($1, $2, $3, $4, $5)"
        params = (chat_id, username, first_name, last_name, lang)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate user, chat ID {chat_id}")
                return False
            except Exception as e:
                logger.error(f"Error while inserting into Users table for chat ID: {chat_id}. Error: {e}")
                return False
        return True

    async def insert_application(
        self,
        chat_id,
        application_number,
        application_suffix,
        application_type,
        application_year,
    ):
        """Insert a new application to the Applications table"""

        logger.debug(
            f"Adding application OAM-{application_number}/{application_type}-{application_year} for chatID {chat_id} to DB"
        )
        query = (
            "INSERT INTO Applications "
            "(user_id, application_number, application_suffix, application_type, application_year, application_state) "
            "SELECT user_id, $2, $3, $4, $5, 'UNKNOWN' FROM Users WHERE chat_id = $1"
        )
        params = (chat_id, application_number, application_suffix, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate application for user {chat_id} and number {application_number}")
                return False
            except Exception as e:
                logger.error(
                    f"Error while inserting into Applications table for user {chat_id}, number: {application_number}. Error: {e}"
                )
                return False
        return True

    async def update_application_status(
        self,
        chat_id,
        application_number,
        application_type,
        application_year,
        current_status,
        is_resolved,
        application_state,
        has_changed,
    ):
        """Update status, is_resolved, changed_at, and state for a specific application"""

        base_query = """
            UPDATE Applications
            SET current_status = $1,
                last_updated = CURRENT_TIMESTAMP,
                is_resolved = $2,
                application_state = $3
                {changed_at_clause}
            WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $4)
              AND application_number = $5
              AND application_type = $6
              AND application_year = $7
        """

        # If the status has changed, add the changed_at clause
        changed_at_clause = ", changed_at = CURRENT_TIMESTAMP" if has_changed else ""
        query = base_query.format(changed_at_clause=changed_at_clause)

        params = (current_status, is_resolved, application_state, chat_id, application_number, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(
                    f"Error while updating DB for chat ID: {chat_id} and application number: {application_number}. Error: {e}"
                )
                return False

    async def update_last_checked(self, chat_id, application_number, application_type, application_year):
        """Update the last_checked timestamp for a specific application for a user"""

        logger.debug(f"Updating last_updated timestamp for chatID {chat_id} and application number {application_number} in DB")
        query = """UPDATE Applications
                   SET last_updated = CURRENT_TIMESTAMP
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2
                   AND application_type = $3
                   AND application_year = $4"""
        params = (chat_id, application_number, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except Exception as e:
                logger.error(
                    f"Error while updating timestamp for user {chat_id} and application number: {application_number}. Error: {e}"
                )

    async def delete_application(self, chat_id, application_number, application_type, application_year):
        """Delete a specific application for a user based on number, type, and year"""

        logger.info(
            f"Removing application OAM-{application_number}/{application_type}-{application_year} for chatID {chat_id} from DB"
        )
        query = """DELETE FROM Applications
                WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                AND application_number = $2
                AND application_type = $3
                AND application_year = $4"""
        params = (chat_id, application_number, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(
                    f"Error while removing application {application_number}, "
                    f"type {application_type}, year {application_year} for user {chat_id}. Error: {e}"
                )
                return False

    async def fetch_user_subscriptions(self, chat_id):
        """Fetch all applications data for a specific user"""

        query = """SELECT *
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)"""
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, chat_id)
                if not rows:
                    logger.info(f"No data found for chat_id {chat_id}")
                    return []
                return [dict(row) for row in rows]  # Convert the records to dictionaries
            except Exception as e:
                logger.error(f"Error while fetching user data for chat ID: {chat_id}. Error: {e}")
                return None

    async def fetch_application_status(self, chat_id, application_number, application_type, application_year):
        """Fetch the status and timestamp of a specific application for a user"""

        query = """SELECT current_status
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2
                   AND application_type = $3
                   AND application_year = $4"""
        params = (chat_id, application_number, application_type, application_year)

        logger.debug("Running status fetch for %s %s %s %s", chat_id, application_number, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchval(query, *params)
                return result
            except Exception as e:
                logger.error(
                    f"Error while fetching application status for user {chat_id} and number: {application_number}. Error: {e}"
                )
                return None

    async def fetch_status_with_timestamp(self, chat_id, application_number, application_type, application_year, lang="EN"):
        """Fetch the status and timestamp of a specific application for a user"""

        query = """SELECT current_status, last_updated
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2
                   AND application_type = $3
                   AND application_year = $4"""
        params = (chat_id, application_number, application_type, application_year)

        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchrow(query, *params)
                if result is not None and result["last_updated"]:
                    current_status = result["current_status"]
                    last_updated_utc = result["last_updated"].replace(tzinfo=pytz.utc)
                    last_updated_prague = last_updated_utc.astimezone(pytz.timezone("Europe/Prague"))
                    timestamp = last_updated_prague.strftime("%H:%M:%S %d-%m-%Y")

                    status_str = message_texts[lang]["current_status_timestamp"].format(
                        status_sign=categorize_application_status(current_status)[1],
                        status=current_status,
                        timestamp=timestamp,
                    )
                    return status_str
                else:
                    return message_texts[lang]["current_status_empty"]
            except Exception as e:
                logger.error(
                    f"Error while fetching status from DB for {chat_id} and application number: {application_number}. Error: {e}"
                )
                return message_texts[lang]["error_generic"]

    async def fetch_applications_needing_update(self, refresh_period, not_found_refresh_period):
        """Fetch applications that need updates based on their refresh periods"""

        refresh_seconds = refresh_period.total_seconds()
        not_found_seconds = not_found_refresh_period.total_seconds()

        query = """
            SELECT u.chat_id, a.application_number, a.application_suffix, a.application_type,
                   a.application_year, a.last_updated, a.application_state
            FROM Applications a
            JOIN Users u ON a.user_id = u.user_id
            WHERE (
                (a.application_state != 'NOT_FOUND' AND
                 EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(a.last_updated, TIMESTAMP '1970-01-01'))) > $1)
                OR
                (a.application_state = 'NOT_FOUND' AND
                 EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(a.last_updated, TIMESTAMP '1970-01-01'))) > $2)
            )
            AND a.is_resolved = FALSE
        """

        async with self.pool.acquire() as conn:
            try:
                return await conn.fetch(query, refresh_seconds, not_found_seconds)
            except Exception as e:
                logger.error(f"Error while fetching applications needing update from DB: {e}")
                return []

    async def fetch_applications_to_expire(self, not_found_max_age):
        """Fetch applications in NOT_FOUND state exceeding the max age"""
        not_found_seconds = not_found_max_age.total_seconds()

        query = """
            SELECT a.application_id, u.chat_id, a.application_number, a.application_suffix,
                   a.application_type, a.application_year, a.created_at
            FROM Applications a
            JOIN Users u ON a.user_id = u.user_id
            WHERE a.application_state = 'NOT_FOUND'
              AND EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - a.created_at)) >= $1
              AND a.is_resolved = FALSE
        """

        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, not_found_seconds)
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"Error while fetching applications to expire from DB: {e}")
                return []

    async def resolve_application(self, application_id):
        """Mark application as resolved"""

        query = """
            UPDATE Applications
            SET is_resolved = TRUE
            WHERE application_id = $1
        """
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, application_id)
                return True
            except Exception as e:
                logger.error(f"Error while marking as resolved application with ID {application_id} in DB: {e}")
                return False

    async def user_exists(self, chat_id):
        """Check if a user exists in the database"""

        query = "SELECT EXISTS(SELECT 1 FROM Users WHERE chat_id = $1)"
        async with self.pool.acquire() as conn:
            try:
                exists = await conn.fetchval(query, chat_id)
                return exists
            except Exception as e:
                logger.error(f"Error while checking if user with chat_id {chat_id} exists in DB: {e}")
                return False

    async def subscription_exists(self, chat_id, application_number, application_type, application_year):
        """Check if a specific application already exists for a user"""

        query = """
            SELECT EXISTS(
                SELECT 1
                FROM Applications a
                JOIN Users u ON a.user_id = u.user_id
                WHERE u.chat_id = $1
                AND a.application_number = $2
                AND a.application_type = $3
                AND a.application_year = $4
            )
        """
        params = (chat_id, application_number, application_type, application_year)

        async with self.pool.acquire() as conn:
            try:
                exists = await conn.fetchval(query, *params)
                return exists
            except Exception as e:
                logger.error(
                    f"Error while checking if subscription exists for user {chat_id} "
                    f"and application number: {application_number}. Error: {e}"
                )
                return False

    async def count_user_subscriptions(self, chat_id):
        """Count the number of subscriptions for a given user"""

        query = "SELECT COUNT(*) FROM Applications WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)"
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query, chat_id)
                return count
            except Exception as e:
                logger.error(f"Error while fetching subscription count for chat ID: {chat_id}. Error: {e}")
                return None

    async def count_users_total(self):
        """Count the total number of users regardless of subscriptions"""

        query = "SELECT COUNT(*) FROM Users"
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching total user count. Error: {e}")
                return None

    async def count_subscribed_users(self):
        """Count users that have at least one subscription"""

        query = """SELECT COUNT(DISTINCT user_id)
                FROM Applications"""
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching count of subscribed users. Error: {e}")
                return None

    async def count_active_users(self):
        """Count users that have at least one subscription which is not in a resolved state"""

        query = """SELECT COUNT(DISTINCT user_id)
                FROM Applications
                WHERE is_resolved = FALSE"""
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching count of active users. Error: {e}")
                return None

    async def fetch_user_language(self, chat_id):
        """Fetch the preferred language for a user"""

        query = "SELECT language FROM Users WHERE chat_id = $1;"
        async with self.pool.acquire() as conn:
            logger.debug(f"Going to DB to fetch language for user: {chat_id}")
            try:
                result = await conn.fetchval(query, chat_id)
                return result
            except Exception as e:
                logger.error(f"Error while fetching language for chat ID: {chat_id}. Error: {e}")
                return None

    async def update_user_language(self, chat_id, lang):
        """Update the preferred language for a user"""

        logger.debug(f"Update user {chat_id} language in DB to {lang}")
        query = "UPDATE Users SET language = $1 WHERE chat_id = $2"
        params = (lang, chat_id)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(f"Error while updating lang in DB for chat ID: {chat_id}. Error: {e}")
                return False

    async def fetch_all_chat_ids(self):
        """Fetch all chat IDs from the Users table"""

        query = "SELECT chat_id FROM Users"
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query)
                # Extract chat_id from each record and return as a list
                return [row["chat_id"] for row in rows]
            except Exception as e:
                logger.error(f"Error while fetching all chat IDs. Error: {e}")
                return []

    async def fetch_user_reminders(self, chat_id):
        """Fetch all reminders for a specific user based on chat_id along with associated application data"""

        logger.debug(f"Fetching all user {chat_id} reminders with associated applications")
        query = """
            SELECT
                r.reminder_id, r.reminder_time,
                a.application_id, a.application_number, a.application_type, a.application_year
            FROM Reminders r
            JOIN Users u ON r.user_id = u.user_id
            JOIN Applications a ON r.application_id = a.application_id
            WHERE u.chat_id = $1;
        """
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, chat_id)
                return [dict(row) for row in rows]  # Convert the records to dictionaries
            except Exception as e:
                logger.error(f"Error while fetching reminders for chat ID: {chat_id}. Error: {e}")
                return []

    async def insert_reminder(self, chat_id: int, time_input: str, application_id: int):
        """Inserts a new reminder into the database"""

        logger.debug(f"Add reminder at {time_input} for user {chat_id}, application_id: {application_id}")

        try:
            # Convert the string to a time object
            time_obj = datetime.datetime.strptime(time_input, "%H:%M").time()
        except ValueError:
            logger.error(f"Invalid time format: {time_input}")
            return False

        query = """
            INSERT INTO Reminders (user_id, reminder_time, application_id)
            SELECT user_id, $2, $3
            FROM Users
            WHERE chat_id = $1;
        """
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, chat_id, time_obj, application_id)
                return True
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate reminder for chat ID {chat_id}")
                return False
            except Exception as e:
                logger.error(f"Error while inserting reminder for chat ID: {chat_id}. Error: {e}")
                return False

    async def delete_reminder(self, chat_id, reminder_id):
        """Delete a specific reminder based on reminder_id"""
        logger.info(f"Removing reminder {reminder_id} for user {chat_id}")

        query = "DELETE FROM Reminders WHERE reminder_id = $1"
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, reminder_id)
                return True
            except Exception as e:
                logger.error(f"Error while deleting reminder with ID: {reminder_id}. Error: {e}")
                return False

    async def fetch_due_reminders(self):
        """Fetch reminders that are due to execute at the current time"""

        # Get current time in UTC
        current_utc_time = datetime.datetime.utcnow()

        # Convert UTC time to Europe/Prague timezone
        prague_timezone = pytz.timezone("Europe/Prague")
        current_prague_time = current_utc_time.astimezone(prague_timezone)

        # Extract the hour and minute
        hour, minute = current_prague_time.hour, current_prague_time.minute

        query = """
            SELECT r.reminder_id, u.chat_id, r.reminder_time, a.application_number,
            a.application_suffix, a.application_type, a.application_year, a.last_updated
            FROM Reminders r
            INNER JOIN Users u ON r.user_id = u.user_id
            INNER JOIN Applications a ON r.application_id = a.application_id
            WHERE a.is_resolved = FALSE
              AND EXTRACT(HOUR FROM r.reminder_time) = $1
              AND EXTRACT(MINUTE FROM r.reminder_time) = $2;
        """
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, hour, minute)
                return [dict(row) for row in rows]  # Convert the records to dictionaries
            except Exception as e:
                logger.error(f"Error while fetching due reminders. Error: {e}")
                return []

    async def count_all_reminders(self):
        """Count the total number of reminders in the database"""

        query = "SELECT COUNT(*) FROM Reminders"
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching total reminders count. Error: {e}")
                return None

    async def count_all_subscriptions(self, active_only=False):
        """Count the total number of subscriptions (applications) in the database, optionally filtering active ones"""
        query = "SELECT COUNT(*) FROM Applications"
        if active_only:
            query += " WHERE is_resolved = FALSE"

        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching total {'active ' if active_only else ''}subscriptions count. Error: {e}")
                return None

    async def close(self):
        logger.info("Shutting down DB connection")
        try:
            await self.pool.close()
        except Exception as e:
            logger.error(f"Error while shutting down DB connection. Error: {e}")
