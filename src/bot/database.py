import asyncpg
import logging
import pytz
import asyncio
from bot.texts import message_texts

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

        logger.info(f"Adding application for chatID {chat_id} to DB")
        query = (
            "INSERT INTO Applications "
            "(user_id, application_number, application_suffix, application_type, application_year) "
            "SELECT user_id, $2, $3, $4, $5 FROM Users WHERE chat_id = $1"
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
        self, chat_id, application_number, application_type, application_year, current_status, is_resolved
    ):
        """Update the status and resolution for a specific application"""

        query = """UPDATE Applications
                   SET current_status = $1, last_updated = CURRENT_TIMESTAMP, is_resolved=$2
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $3)
                   AND application_number = $4
                   AND application_type = $5
                   AND application_year = $6"""
        params = (current_status, is_resolved, chat_id, application_number, application_type, application_year)
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
                    return None
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

    async def fetch_applications_needing_update(self, refresh_period):
        """Fetch applications that need updates based on the refresh period"""

        # Convert the timedelta refresh period to seconds for the SQL interval
        seconds = refresh_period.total_seconds()

        # Fetch rows where the current time minus last_checked is more than the refresh period
        query = """
            SELECT u.chat_id, a.application_number, a.application_suffix, a.application_type, a.application_year, a.last_updated
            FROM Applications a
            JOIN Users u ON a.user_id = u.user_id
            WHERE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(a.last_updated, TIMESTAMP '1970-01-01'))) > $1
            AND a.is_resolved = FALSE
        """

        async with self.pool.acquire() as conn:
            try:
                return await conn.fetch(query, seconds)
            except Exception as e:
                logger.error(f"Error while fetching applications needing update. Error: {e}")
                return []

    async def user_exists(self, chat_id):
        """Check if a user exists in the database"""

        query = "SELECT EXISTS(SELECT 1 FROM Users WHERE chat_id = $1)"
        async with self.pool.acquire() as conn:
            try:
                exists = await conn.fetchval(query, chat_id)
                return exists
            except Exception as e:
                logger.error(f"Error while checking if user with chat_id {chat_id} exists. Error: {e}")
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

    async def close(self):
        logger.info("Shutting down DB connection")
        try:
            await self.pool.close()
        except Exception as e:
            logger.error(f"Error while shutting down DB connection. Error: {e}")
