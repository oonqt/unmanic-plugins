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
        Create a stereo AAC stream if this stream is multichannel and no stereo track
        of the same language exists.
        """
        language = stream_info.get('tags', {}).get('language', 'und')
        channels = stream_info.get('channels', 0)
        codec = stream_info.get('codec_name', '').lower()
        bitrate = int(stream_info.get('bit_rate', 0))

        # Skip if not multichannel
        if channels <= 2:
            return None

        # Skip if there's already a stereo track of same language
        for s in self._get_streams_from_probe():
            if s.get('tags', {}).get('language', 'und') == language and s.get('channels', 0) <= 2:
                return None

        # Get target bitrate (in kbps)
        try:
            target_bitrate_kbps = int(self.settings.get_setting('target_bitrate_kbps'))
        except (TypeError, ValueError):
            target_bitrate_kbps = 256  # fallback default

        # Determine final bitrate (min of source and target)
        source_kbps = bitrate // 1000 if bitrate > 0 else target_bitrate_kbps
        final_bitrate_kbps = min(source_kbps, target_bitrate_kbps)
        final_bitrate_str = f"{final_bitrate_kbps}k"

        # Build encoding arguments
        stream_encoding = [
            f"-c:a:{stream_id}", "aac",
            f"-b:a:{stream_id}", final_bitrate_str,
            f"-metadata:s:a:{stream_id}", f"language={language}",
            f"-disposition:a:{stream_id}", "default"
        ]

        # Map the original stream
        stream_mapping = ['-map', f"0:a:{stream_info['index']}"]

        # Downmix filter (no hardcoded :a:1 nonsense)
        custom_filter = (
            f"[0:a:{stream_info['index']}]"
            "pan=stereo|c0=c2+0.30*c0+0.30*c4|c1=c2+0.30*c1+0.30*c5"
            f"[dm{stream_id}]"
        )
        self.filter_complex.append(custom_filter)

        return {
            'stream_mapping': stream_mapping,
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