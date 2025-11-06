#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    plugins.__init__.py

    Written by:               Rechigo

    Copyright:
        Copyright (C) 2024 Jay Gardner

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""
import logging
import os

from unmanic.libs.unplugins.settings import PluginSettings

from convert_dts_stereo_to_flac.lib.ffmpeg import Probe, Parser

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.convert_dts_stereo_to_flac")

class Settings(PluginSettings):
    settings = {
        "compression_level": 6
    }
     
    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "compression_level": {
                "label": "Compression Level",
                "input_type": "slider",
                "slider_options": {
                    "step": 1,
                    "min": 0,
                    "max": 8
                },
                "tooltip": "Lower values encode faster but produce larger files. Higher values compress better but take longer.",
            }
        }

def s2_encode(probe_streams, abspath):
    """
    Identify DTS/TrueHD audio streams that are mono (1 channel) or stereo (2 channels).

    Returns:
      - dts_streams_list: list of absolute probe indices for targeted dts/truehd streams (mono or stereo)
      - all_astreams: list of absolute probe indices for all audio streams
    """
    try:
        # target mono or stereo DTS / TrueHD tracks (1 or 2 channels)
        dts_streams_list = [
            i for i in range(0, len(probe_streams))
            if "codec_type" in probe_streams[i]
            and probe_streams[i]["codec_type"] == 'audio'
            and int(probe_streams[i].get("channels", 0)) in (1, 2)
            and probe_streams[i].get("codec_name", "").lower() in ["dts", "truehd"]
        ]

        all_astreams = [
            i for i in range(0, len(probe_streams))
            if "codec_type" in probe_streams[i] and probe_streams[i]["codec_type"] == 'audio'
        ]
        return dts_streams_list, all_astreams
    except Exception:
        logger.info("No mono/stereo DTS/TrueHD audio streams found to encode")
        return [],[]

def on_library_management_file_test(data):
    """
    Decide whether to add file to pending tasks based on presence of mono/stereo DTS/TrueHD streams.
    """
    # Get the path to the file
    abspath = data.get('path')

    # Get file probe
    probe_data = Probe(logger, allowed_mimetypes=['audio', 'video'])

    # Get stream data from probe
    if probe_data.file(abspath):
        probe_streams = probe_data.get_probe()["streams"]
        probe_format = probe_data.get_probe()["format"]
    else:
        logger.debug("Probe data failed - Blocking everything.")
        data['add_file_to_pending_tasks'] = False
        return data

    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    stream_to_encode, all_astreams = s2_encode(probe_streams, abspath)

    if stream_to_encode:
        data['add_file_to_pending_tasks'] = True
        for i in range(len(all_astreams)):
            if all_astreams[i] in stream_to_encode:
                # get channel count for the targeted absolute stream index
                abs_idx = all_astreams[i]
                ch = int(probe_streams[abs_idx].get("channels", 2))
                ch_text = "mono (1ch)" if ch == 1 else "stereo (2ch)" if ch == 2 else f"{ch}ch"
                logger.info(
                    "audio stream '{}' is {} DTS/TrueHD and will be re-encoded to FLAC (replacing original DTS audio stream)".format(i, ch_text)
                )
    else:
        logger.info(
            "do not add file '{}' to task list - no mono or stereo (1 or 2 channel) DTS/TrueHD audio streams found".format(abspath)
        )

    return data


def on_worker_process(data):
    """
    Build the ffmpeg command to convert targeted mono/stereo DTS/TrueHD audio streams to FLAC
    with the configured compression level, preserving mono/stereo channel count, and copy other streams.
    """
    # Default to no FFMPEG command required. This prevents the FFMPEG command from running if it is not required
    data['exec_command'] = []
    data['repeat'] = False

    # Get the path to the file
    abspath = data.get('file_in')
    outpath = data.get('file_out')

    # Get file probe
    probe_data = Probe(logger, allowed_mimetypes=['audio', 'video'])

    if probe_data.file(abspath):
        probe_streams = probe_data.get_probe()["streams"]
        probe_format = probe_data.get_probe()["format"]
    else:
        logger.debug("Probe data failed - Nothing to encode - '{}'".format(abspath))
        return data

    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    # find streams to encode
    stream_to_encode, all_astreams = s2_encode(probe_streams, abspath)

    # Nothing to do
    if not stream_to_encode:
        return data

    # get compression level from settings (validate & clamp to 0-8)
    try:
        comp_level_raw = settings.get_setting('compression_level')
    except Exception:
        # fallback if get_setting isn't available
        comp_level_raw = Settings.settings.get('compression_level', '6')

    try:
        comp_level = int(comp_level_raw)
    except (TypeError, ValueError):
        comp_level = 6

    if comp_level < 0:
        comp_level = 0
    if comp_level > 8:
        comp_level = 8

    logger.debug("Using FLAC compression level: %s", comp_level)

    # Set initial ffmpeg args
    ffmpeg_args = ['-hide_banner', '-loglevel', 'info', '-i', str(abspath), '-max_muxing_queue_size', '9999', '-strict', '-2']

    # set stream maps: copy video, convert targeted audio streams to FLAC (preserve mono/stereo), copy other audio and subtitle/data/timetrack streams
    stream_map = ['-map', '0:v', '-c:v', 'copy']
    for i in range(len(all_astreams)):
        abs_idx = all_astreams[i]
        if abs_idx in stream_to_encode:
            # determine channels for this absolute stream index
            try:
                channels = int(probe_streams[abs_idx].get("channels", 2))
            except Exception:
                channels = 2
            # ensure we set 1 for mono, 2 for stereo (fallback to 2)
            if channels == 1:
                target_ac = '1'
                title = 'FLAC Mono'
            else:
                target_ac = '2'
                title = 'FLAC Stereo'

            # convert this audio stream to flac, ensure correct channel count, set compression level
            # use per-stream codec spec '-c:a:<idx> flac' followed by '-compression_level' (applies to next audio encodes)
            stream_map += [
                '-map', '0:a:' + str(i),
                '-c:a:' + str(i), 'flac',
                '-compression_level', str(comp_level),
                '-sample_fmt', 's16',
                '-ac:' + str(i), target_ac,
                '-metadata:s:a:' + str(i), 'title=' + title
            ]
        else:
            # copy other audio streams unchanged
            stream_map += ['-map', '0:a:' + str(i), '-c:a:' + str(i), 'copy']

    # copy subtitles, attachments, data and text streams if present
    stream_map += ['-map', '0:s?', '-c:s', 'copy', '-map', '0:d?', '-c:d', 'copy', '-map', '0:t?', '-c:t', 'copy']
    ffmpeg_args += stream_map

    # Get suffix and add remove chapters in case of mp4
    sfx = os.path.splitext(abspath)[1]
    if sfx == '.mp4':
        ffmpeg_args += ['-dn', '-map_metadata:c', '-1']

    # Add final ffmpeg_args
    ffmpeg_args += ['-y', str(outpath)]
    logger.debug("ffmpeg args: '%s'", ffmpeg_args)

    # Apply ffmpeg args to command
    data['exec_command'] = ['ffmpeg']
    data['exec_command'] += ffmpeg_args

    # Set the parser
    parser = Parser(logger)
    parser.set_probe(probe_data)
    data['command_progress_parser'] = parser.parse_progress

    return data
