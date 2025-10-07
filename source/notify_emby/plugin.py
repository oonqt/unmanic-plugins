#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    Written by:               yajrendrag <yajdude@gmail.com>
    Date:                     26 January 2023, (10:00 AM)

    Copyright:
        Copyright (C) 2023 Jay Gardner

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""
import logging
import requests
import time
from unmanic.libs.unplugins.settings import PluginSettings

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.notify_emby")

class Settings(PluginSettings):
    settings = {
        "emby_url": "http://localhost:8096",
        "emby_key": "XXXXXX",
        "plugin_delay": 0
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "emby_url": {
                "label": "Enter the url to your Emby server",
            },
            "emby_key": {
                "label": "Enter your Emby API key"
            },
            "plugin_delay": {
                "label": "The amount of time in seconds to delay proceeding post-processing queue item (ie. sonarr, radarr). Leave at 0 to disable."
            }
        }


def update_emby(emby_url, emby_key, file_path):
    headers = {"X-MediaBrowser-Token": emby_key}
    data = {
        "Updates": [
            {
                "Path": file_path
            }
        ]
    }

    try:
        r = requests.post(emby_url + "/emby/Library/Media/Updated", headers=headers, json=data)
    except (ConnectionRefusedError, requests.exceptions.ConnectionError) as error:
        logger.error("Error Connecting to Emby - unable to reach or unauthorized")
        
    if r.status_code == 204:
        logger.info("Notified Emby to update item: {}".format(file_path))
    else:
        logger.error("Error notifying Emby - Error Code:('{}').".format(r.status_code))

def on_postprocessor_task_results(data):
    """
    Runner function - provides a means for additional postprocessor functions based on the task success.

    The 'data' object argument includes:
        task_processing_success         - Boolean, did all task processes complete successfully.
        file_move_processes_success     - Boolean, did all postprocessor movement tasks complete successfully.
        destination_files               - List containing all file paths created by postprocessor file movements.
        source_data                     - Dictionary containing data pertaining to the original source file.

    :param data:
    :return:

    """
    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    destination_files = data.get('destination_files') 
    if (len(destination_files) == 0):
        logger.info("No destination file found. Skipping Emby update")
        return data

    file_path = destination_files[0] # May cause issues when multiple files are extracted from one (ie embedded subtitles extracted to external), but we assume input file wil always be the first item
    emby_url = settings.get_setting("emby_url")
    emby_key = settings.get_setting("emby_key")
    plugin_delay = settings.get_setting("plugin_delay")
    update_emby(emby_url, emby_key, file_path)

    logger.info("Sleeping for {} seconds before proceeding to next task".format(plugin_delay))

    time.sleep(int(plugin_delay))

    return data
