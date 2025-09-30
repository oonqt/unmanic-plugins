#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    plugins.__init__.py

    Written by:               yajrendrag <yajdude@gmail.com>
    Date:                     26 Feb 2024, (1:00 PM)

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

from convert_dts_to_eac3.lib.ffmpeg import Probe, Parser

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.convert_dts_to_eac3")


class Settings(PluginSettings):
    settings = {
        "bit_rate": "640k",
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)


def s2_analyze(probe_streams, abspath):
    """
    Analyze probe_streams and decide whether to:
      - 'drop' DTS multichannel streams (if another non-DTS multi-channel stream exists),
      - 'convert' DTS multichannel streams to eac3 (if the only other audio tracks are stereo),
      - or None (nothing to do).

    Returns:
        dts_indices: list of absolute stream indices for DTS multichannel streams (channels > 2)
        all_astreams: list of absolute stream indices for all audio streams (in file order)
        action: one of 'drop', 'convert', or None
    """
    try:
        # dts detection: codec_name contains 'dts' or 'dca'
        DTS_HINTS = ("dts", "dca")

        all_astreams = [
            i for i in range(0, len(probe_streams))
            if "codec_type" in probe_streams[i] and probe_streams[i]["codec_type"] == 'audio'
        ]

        dts_indices = []
        non_dts_multichannel = []

        for i in all_astreams:
            s = probe_streams[i]
            codec = (s.get("codec_name") or "").lower()
            channels = int(s.get("channels") or 0)

            is_dts = any(k in codec for k in DTS_HINTS)
            is_multichannel = channels > 2  # non-stereo = channels > 2

            if is_dts and is_multichannel:
                dts_indices.append(i)
            elif (not is_dts) and is_multichannel:
                non_dts_multichannel.append(i)

        # Decision logic:
        # - If there's any non-DTS multichannel stream, drop DTS multichannel track(s).
        # - Else, if there are DTS multichannel streams, convert them to eac3.
        if non_dts_multichannel:
            action = 'drop'
        elif dts_indices:
            action = 'convert'
        else:
            action = None

        return dts_indices, all_astreams, action
    except Exception:
        logger.info("No DTS audio streams found to inspect in '{}'".format(abspath))
        return [], [], None


def on_library_management_file_test(data):
    """
    Runner function - enables additional actions during the library management file tests.
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

    dts_indices, all_astreams, action = s2_analyze(probe_streams, abspath)

    if action in ('drop', 'convert'):
        data['add_file_to_pending_tasks'] = True
        for audio_pos, abs_idx in enumerate(all_astreams):
            if abs_idx in dts_indices:
                if action == 'convert':
                    logger.info("audio stream '{}' (file audio pos {}) is DTS and will be re-encoded as eac3 (replacing original DTS).".format(abs_idx, audio_pos))
                else:  # drop
                    logger.info("audio stream '{}' (file audio pos {}) is DTS and will be discarded (another non-stereo multichannel audio stream exists).".format(abs_idx, audio_pos))
    else:
        logger.info("do not add file '{}' to task list - no multichannel DTS audio streams requiring action found".format(abspath))

    return data


def on_worker_process(data):
    """
    Runner function - enables additional configured processing jobs during the worker stages of a task.
    """
    # Default to no FFMPEG command required.
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

    dts_indices, all_astreams, action = s2_analyze(probe_streams, abspath)
    bit_rate = settings.get_setting('bit_rate')

    if action in ('drop', 'convert'):
        encoder = 'eac3'

        # Set initial ffmpeg args
        ffmpeg_args = ['-hide_banner', '-loglevel', 'info', '-i', str(abspath), '-max_muxing_queue_size', '9999', '-strict', '-2']

        # map video first
        stream_map = ['-map', '0:v', '-c:v', 'copy']

        # audio mapping:
        # audio_pos is the audio stream index in file order (0:a:0, 0:a:1, ...)
        # out_a_idx is the output audio stream index (used for -c:a:N), increments only when we actually map an audio stream.
        out_a_idx = 0
        for audio_pos, abs_idx in enumerate(all_astreams):
            # If action == 'drop' and this abs_idx is a DTS stream to be dropped, skip mapping it entirely.
            if action == 'drop' and abs_idx in dts_indices:
                logger.info("Skipping mapping of DTS audio (abs stream {}) because a non-DTS multichannel stream exists.".format(abs_idx))
                continue

            # Map this audio stream
            stream_map += ['-map', '0:a:{}'.format(audio_pos)]

            # If action == 'convert' and this abs_idx is a DTS stream, transcode it to eac3
            if action == 'convert' and abs_idx in dts_indices:
                # set codec for this output audio index
                stream_map += ['-c:a:{}'.format(out_a_idx), encoder, '-ac', '6', '-b:a:{}'.format(out_a_idx), bit_rate, '-metadata:s:a:{}'.format(out_a_idx), 'title={}'.format(encoder + ' Surround')]
                logger.info("Mapping DTS audio (abs stream {}) -> re-encode to {} (out audio idx {}).".format(abs_idx, encoder, out_a_idx))
            else:
                # copy other audio streams
                stream_map += ['-c:a:{}'.format(out_a_idx), 'copy']
                logger.info("Mapping audio (abs stream {}) -> copy (out audio idx {}).".format(abs_idx, out_a_idx))

            out_a_idx += 1

        # map subtitles, attachments, chapters etc.
        stream_map += ['-map', '0:s?', '-c:s', 'copy', '-map', '0:d?', '-c:d', 'copy', '-map', '0:t?', '-c:t', 'copy']
        ffmpeg_args += stream_map

        # Get suffix and add remove chapters in case of mp4
        sfx = os.path.splitext(abspath)[1]
        if sfx == '.mp4':
            ffmpeg_args += ['-dn', '-map_metadata:c', '-1']

        # Add final ffmpeg_args
        ffmpeg_args += ['-y', str(outpath)]
        logger.debug("ffmpeg args: '{}'".format(ffmpeg_args))

        # Apply ffmpeg args to command
        data['exec_command'] = ['ffmpeg']
        data['exec_command'] += ffmpeg_args

        # Set the parser
        parser = Parser(logger)
        parser.set_probe(probe_data)
        data['command_progress_parser'] = parser.parse_progress
    else:
        logger.debug("No action required for '{}'".format(abspath))

    return data
