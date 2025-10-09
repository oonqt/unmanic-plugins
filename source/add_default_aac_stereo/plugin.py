#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    unmanic-plugins.plugin.py

    Written by:               k29t59dh <chapels.rill_0h@icloud.com>
    Date:                     9 Aug 2023, (4:06 PM)

    Copyright:
        Copyright (C) 2021 Josh Sunnex

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""

import logging, os

from unmanic.libs.unplugins.settings import PluginSettings
from add_default_aac_stereo.lib.ffmpeg import StreamMapper, Probe, Parser

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.add_default_aac_stereo")


class Settings(PluginSettings):
    settings = {
        "custom_formula": False,
        "formula": "pan=stereo|c0=c2+0.30*c0+0.30*c4|c1=c2+0.30*c1+0.30*c5",
        # target bitrate in kbps for downmixed AAC output
        "target_bitrate_kbps": 256,
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "custom_formula": {
                "label": "Custom formula",
            },
            "formula":        self.__set_formula_form_settings(),
            "target_bitrate_kbps": {
                "label": "Target AAC bitrate (kbps)",
                "help": "Target maximum bitrate for generated AAC stereo tracks. If the source bitrate is lower, the source bitrate will be used."
            },
        }

    def __set_formula_form_settings(self):
        values = {
            "label":       "Formula",
        }
        if not self.get_setting('custom_formula'):
            values["display"] = 'hidden'
        return values


class PluginStreamMapper(StreamMapper):
    def __init__(self):
        super(PluginStreamMapper, self).__init__(logger, ['audio'])
        self.codec = 'aac'
        self.encoder = 'aac'
        self.settings = None

    def set_default_values(self, settings, abspath, probe):
        """
        Configure the stream mapper with defaults

        :param settings:
        :param abspath:
        :param probe:
        :return:
        """
        self.abspath = abspath
        # Set the file probe data
        self.set_probe(probe)
        # Set the input file
        self.set_input_file(abspath)
        # Configure settings
        self.settings = settings


    def _get_streams_from_probe(self):
        """
        Helper to extract probe streams in a tolerant way.
        """
        try:
            # common APIs: probe.get_probe() -> dict with 'streams'
            probe_data = None
            if hasattr(self, 'probe') and self.probe:
                if hasattr(self.probe, 'get_probe'):
                    probe_data = self.probe.get_probe()
                elif hasattr(self.probe, 'probe'):
                    probe_data = getattr(self.probe, 'probe')
                elif hasattr(self.probe, 'data'):
                    probe_data = getattr(self.probe, 'data')
                else:
                    probe_data = self.probe
            if isinstance(probe_data, dict):
                return probe_data.get('streams', [])
            if isinstance(probe_data, list):
                return probe_data
        except Exception:
            logger.debug("Unable to extract streams from probe for additional logic.")
        return []


    def _stream_language(self, stream_info: dict):
        """
        Return language tag for a stream (normalized). Falls back to 'und'.
        """
        tags = stream_info.get('tags') or {}
        lang = tags.get('language') or tags.get('LANGUAGE') or tags.get('lang') or tags.get('Lang') or 'und'
        if not lang:
            return 'und'
        return lang.strip().lower()


    def _get_stream_bitrate_kbps(self, stream_info: dict):
        """
        Try to extract the stream bitrate (kbps) from the probe. Returns an int kbps or None.
        Common ffprobe field: 'bit_rate' (bits per second).
        """
        try:
            br = stream_info.get('bit_rate') or stream_info.get('avg_frame_rate') or None
            if br:
                # bit_rate is often a string like '192000' (bits/s)
                try:
                    br_int = int(br)
                    kbps = int(round(br_int / 1000.0))
                    return kbps
                except Exception:
                    # Sometimes bit_rate may be a string with units or not parseable; ignore
                    pass
            # Some probes may include codec-specific nested fields or tags; attempt tags
            tags = stream_info.get('tags') or {}
            tag_br = tags.get('BPS') or tags.get('BITRATE') or tags.get('bitrate')
            if tag_br:
                try:
                    br_int = int(tag_br)
                    kbps = int(round(br_int / 1000.0))
                    return kbps
                except Exception:
                    # If it's like '192k' handle that
                    if isinstance(tag_br, str) and tag_br.lower().endswith('k'):
                        try:
                            return int(tag_br[:-1])
                        except Exception:
                            pass
        except Exception:
            logger.debug("Failed to parse bit rate from stream info.")
        return None


    def test_stream_needs_processing(self, stream_info: dict):
        """
        Decide whether a given stream should have a downmixed stereo added.

        New logic:
        - Only consider multichannel streams (channels > 2).
        - Do NOT add a downmix if there already exists a stereo/mono track with the same language.
        - If the stream is already AAC+stereo (or mono) do not process.
        - Languages must match (we check tags.language on streams).
        """
        codec = (stream_info.get('codec_name') or '').lower()
        channels = int(stream_info.get('channels', 2))

        # if stream is already AAC and stereo (or mono) it doesn't need downmixing
        if codec == self.codec and channels <= 2:
            return False

        # Only add downmix tracks for multichannel source streams
        if channels <= 2:
            # Not multichannel; we do not create a new stereo for it.
            return False

        # get this stream's language (normalized)
        this_lang = self._stream_language(stream_info)

        # check probe for any existing stereo/mono stream with matching language
        probe_streams = self._get_streams_from_probe()
        for s in probe_streams:
            if (s.get('codec_type') or '').lower() != 'audio':
                continue
            s_channels = int(s.get('channels', 2))
            if s_channels <= 2:
                s_lang = self._stream_language(s)
                if s_lang == this_lang:
                    # Found an existing stereo/mono audio stream with same language:
                    # we do NOT need to add a downmixed stereo for this language.
                    logger.debug("Skipping downmix for language '%s' because a stereo/mono stream already exists.", this_lang)
                    return False

        # No same-language stereo/mono found and this stream is multichannel:
        # we should add a downmixed stereo (and mark as default).
        logger.debug("Stream (lang=%s, channels=%d) requires a downmixed stereo to be added.", this_lang, channels)
        return True


    def custom_stream_mapping(self, stream_info: dict, stream_id: int):
        """
        Build the mapping/encoding args for ffmpeg for the stream that needs the
        downmixed stereo. We attach language metadata and set the disposition to default.

        Also: determine bitrate to use:
         - If the source bitrate (kbps) is present and LOWER than target -> use source kbps
         - Otherwise use target bitrate (kbps)
        """
        # Prepare encoder
        stream_encoding = ['-c:a:{}'.format(stream_id), self.encoder]

        # Build filter (the user's formula from settings)
        formula = self.settings.get_setting('formula') or ''
        # Attach filter as -filter:a:<index> <formula>
        custom_options = '-filter:a:{} '.format(stream_id)
        custom_options += formula
        stream_encoding += custom_options.split()

        # Determine bitrate choice
        try:
            target_kbps = int(self.settings.get_setting('target_bitrate_kbps') or 0)
        except Exception:
            target_kbps = 0

        source_kbps = self._get_stream_bitrate_kbps(stream_info)
        if source_kbps is not None and source_kbps > 0 and target_kbps > 0:
            chosen_kbps = source_kbps if source_kbps < target_kbps else target_kbps
        elif target_kbps > 0:
            # fallback to target if source not known
            chosen_kbps = target_kbps
        elif source_kbps is not None:
            chosen_kbps = source_kbps
        else:
            # absolute fallback
            chosen_kbps = 256

        bitrate_arg = '{}k'.format(int(chosen_kbps))
        logger.debug("Selected bitrate for downmixed stream (lang=%s): %s kbps (source=%s kbps, target=%s kbps)",
                     self._stream_language(stream_info), chosen_kbps, source_kbps, target_kbps)

        # Add bitrate argument for this output audio stream
        try:
            stream_encoding += ['-b:a:{}'.format(stream_id), bitrate_arg]
        except Exception:
            logger.debug("Could not append bitrate argument for stream %s", stream_id)

        # Get language for the stream (so we can set metadata on the new stereo track)
        lang = self._stream_language(stream_info)

        # Add language metadata for this output stream (metadata expects 'language' as a value)
        # Example ffmpeg usage: -metadata:s:a:<index> language=eng
        try:
            stream_encoding += ['-metadata:s:a:{}'.format(stream_id), 'language={}'.format(lang)]
        except Exception:
            logger.debug("Could not append language metadata argument for stream %s", stream_id)

        # Mark this added track as default disposition
        try:
            stream_encoding += ['-disposition:a:{}'.format(stream_id), 'default']
        except Exception:
            logger.debug("Could not append disposition argument for stream %s", stream_id)

        # Return mapping + encoding. The StreamMapper base class should
        # handle integrating these into the final command (mapping the input stream
        # and adding the codec / filter / metadata).
        return {
            'stream_mapping':  ['-map', '0:a:{}'.format(stream_id)],
            'stream_encoding': stream_encoding,
        }


def on_library_management_file_test(data):
    """
    Runner function - enables additional actions during the library management file tests.

    The 'data' object argument includes:
        path                            - String containing the full path to the file being tested.
        issues                          - List of currently found issues for not processing the file.
        add_file_to_pending_tasks       - Boolean, is the file currently marked to be added to the queue for processing.

    :param data:
    :return:

    """
    # Get the path to the file
    abspath = data.get('path')

    # Get file probe
    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(abspath):
        # File probe failed, skip the rest of this test
        return data

    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    # Get stream mapper
    mapper = PluginStreamMapper()
    mapper.set_default_values(settings, abspath, probe)

    if mapper.streams_need_processing():
        # Mark this file to be added to the pending tasks
        data['add_file_to_pending_tasks'] = True
        logger.debug("File '{}' should be added to task list. Probe found streams require processing.".format(abspath))
    else:
        logger.debug("File '{}' does not contain streams require processing.".format(abspath))

    return data


def on_worker_process(data):
    """
    Runner function - enables additional configured processing jobs during the worker stages of a task.

    The 'data' object argument includes:
        exec_command            - A command that Unmanic should execute. Can be empty.
        command_progress_parser - A function that Unmanic can use to parse the STDOUT of the command to collect progress stats. Can be empty.
        file_in                 - The source file to be processed by the command.
        file_out                - The destination that the command should output (may be the same as the file_in if necessary).
        original_file_path      - The absolute path to the original file.
        repeat                  - Boolean, should this runner be executed again once completed with the same variables.

    :param data:
    :return:

    """
    # Default to no FFMPEG command required. This prevents the FFMPEG command from running if it is not required
    data['exec_command'] = []
    data['repeat'] = False

    # Get the path to the file
    abspath = data.get('file_in')

    # Get file probe
    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(abspath):
        # File probe failed, skip the rest of this test
        return data

    # Configure settings object (maintain compatibility with v1 plugins)
    settings = Settings(library_id=data.get('library_id'))

    # Get stream mapper
    mapper = PluginStreamMapper()
    mapper.set_default_values(settings, abspath, probe)

    if mapper.streams_need_processing():
        # Set the input file
        mapper.set_input_file(abspath)

        # Set the output file
        mapper.set_output_file(data.get('file_out'))

        # Get generated ffmpeg args
        ffmpeg_args = mapper.get_ffmpeg_args()

        # Apply ffmpeg args to command
        data['exec_command'] = ['ffmpeg']
        data['exec_command'] += ffmpeg_args

        # Set the parser
        parser = Parser(logger)
        parser.set_probe(probe)
        data['command_progress_parser'] = parser.parse_progress

    return data