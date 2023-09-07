import psycopg2
import logging
import os


logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        DB_HOST = os.getenv("DB_HOST", "localhost")
        DB_NAME = os.getenv("DB_NAME", "AppTrackerDB")
        DB_USER = os.getenv("DB_USER", "postgres")
        DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
        self.conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port="5432")
        logger.info("Connected to the db")

    async def add_to_db(self, chat_id, application_number, application_suffix, application_type, application_year):
        with self.conn.cursor() as cur:
            logger.info(f"Adding chatID {chat_id} with application number {application_number} to DB")
            query = (
                "INSERT INTO Applications "
                "(chat_id, application_number, application_suffix, application_type, application_year) "
                "VALUES (%s, %s, %s, %s, %s)"
            )
            params = (chat_id, application_number, application_suffix, application_type, application_year)
            try:
                cur.execute(query, params)
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                logger.error(f"Error inserting into DB: {e}")
            finally:
                cur.close()

    async def update_db_status(self, chat_id, current_status):
        with self.conn.cursor() as cur:
            logger.info(f"Updating chatID {chat_id} current status in DB")
            query = (
                "UPDATE Applications SET current_status = %s, "
                "status_changed = True, last_updated = CURRENT_TIMESTAMP WHERE chat_id = %s"
            )
            params = (current_status, chat_id)
            try:
                cur.execute(query, params)
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                logger.error(f"Error updating DB: {e}")
            finally:
                cur.close()

    async def remove_from_db(self, chat_id):
        with self.conn.cursor() as cur:
            logger.info(f"Removing chatID {chat_id} from DB")
            try:
                cur.execute("DELETE FROM Applications WHERE chat_id = %s", (chat_id,))
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                logger.error(f"Error deleting from DB: {e}")
            finally:
                cur.close()

    async def get_status_from_db(self, chat_id):
        with self.conn.cursor() as cur:
            try:
                with self.conn.cursor() as cur:
                    cur.execute("""SELECT current_status, last_updated FROM Applications WHERE chat_id = %s;""", (chat_id,))
                    result = cur.fetchone()
                    if result is not None:
                        current_status, last_updated = result
                        return f"Current Status: {current_status}\nLast Updated: <b>{last_updated} UTC</b>"
                    else:
                        return "No data found."
            except Exception as e:
                print(f"Error while fetching data from the database: {e}")
                return "Error while fetching data."

    async def check_subscription_in_db(self, chat_id):
        with self.conn.cursor() as cur:
            try:
                cur.execute("SELECT * FROM Applications WHERE chat_id = %s", (chat_id,))
                return bool(cur.fetchone())
            except Exception as e:
                logger.error(f"Error querying DB: {e}")
                return False
            finally:
                cur.close()

    async def close(self):
        if self.conn:
            logger.info("Shutting down DB connection")
            self.conn.close()
            self.conn = None
