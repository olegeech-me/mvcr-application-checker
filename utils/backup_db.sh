#!/bin/bash

BACKUP_DIR="/opt/backup"
BACKUP_FILE="$BACKUP_DIR/mvcr_$(date +'%Y_%m_%d_%H_%M_%S').sql"

# Create the backup directory if it doesn't exist
if [ ! -d "/opt/backup" ]; then
    mkdir /opt/backup
fi

# Use docker exec to run pg_dump within the container
docker exec postgres pg_dump --user=apptracker_db_admin AppTrackerDB > $BACKUP_FILE

# Remove backups if there are more than 5
cd $BACKUP_DIR
ls -t | grep backup_ | tail -n +6 | xargs rm -f
