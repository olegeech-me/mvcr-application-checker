import asyncpg
import logging


logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dbname, user, password, host, port, loop):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.loop = loop
        self.pool = loop.run_until_complete(
            asyncpg.create_pool(
                database=dbname,
                user=user,
                password=password,
                host=host,
                port=port,
            )
        )
        logger.info("Connected to the db")

    async def add_to_db(self, chat_id, application_number, application_suffix, application_type, application_year):
        logger.info(f"Adding chatID {chat_id} with application number {application_number} to DB")
        query = (
            "INSERT INTO Applications "
            "(chat_id, application_number, application_suffix, application_type, application_year) "
            "VALUES ($1, $2, $3, $4, $5)"
        )
        params = (chat_id, application_number, application_suffix, application_type, application_year)
        try:
            await self.pool.execute(query, *params)
        except asyncpg.UniqueViolationError:
            logger.error(f"Attempt to insert duplicate chat ID {chat_id} and application number {application_number}")
        except Exception as e:
            logger.error(
                f"Error while inserting into DB for chat ID: {chat_id} for application number {application_number}. Error: {e}"
            )

    async def update_db_status(self, chat_id, current_status):
        logger.info(f"Updating chatID {chat_id} current status in DB")
        query = (
            "UPDATE Applications SET current_status = $1, "
            "status_changed = True, last_updated = CURRENT_TIMESTAMP WHERE chat_id = $2"
        )
        params = (current_status, chat_id)
        try:
            await self.pool.execute(query, *params)
        except Exception as e:
            logger.error(f"Error while updating DB for chat ID: {chat_id}. Error: {e}")

    async def remove_from_db(self, chat_id):
        logger.info(f"Removing chatID {chat_id} from DB")
        query = "DELETE FROM Applications WHERE chat_id = $1"
        try:
            await self.pool.execute(query, chat_id)
        except Exception as e:
            logger.error(f"Error while updating DB for chat ID: {chat_id}. Error: {e}")

    async def get_status_from_db(self, chat_id):
        query = "SELECT current_status, last_updated FROM Applications WHERE chat_id = $1;"
        try:
            result = await self.pool.fetchrow(query, chat_id)
            if result is not None:
                current_status, last_updated = result["current_status"], result["last_updated"]
                return f"Current Status: {current_status}\nLast Updated: <b>{last_updated} UTC</b>"
            else:
                return "No data found."
        except Exception as e:
            logger.error(f"Error while fetching status from DB for chat ID: {chat_id}. Error: {e}")
            return "Error fetching data"

    async def check_subscription_in_db(self, chat_id):
        query = "SELECT EXISTS(SELECT chat_id FROM Applications WHERE chat_id=$1)"
        try:
            result = await self.pool.fetchval(query, chat_id)
            return result
        except Exception as e:
            logger.error(f"Error while checking chat_id {chat_id} subscription. Error: {e}")
            return False

    async def close(self):
        logger.info("Shutting down DB connection")
        try:
            await self.pool.close()
        except Exception as e:
            logger.error(f"Error while shutting down DB connection. Error: {e}")
