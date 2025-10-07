#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    plugins.__init__.py

    Plugin behaviour: Remove stereo (2-channel) audio streams when there exists
    a multichannel (>2) audio stream with the same language.

    NOTE: This file drops backwards compatibility with the previous
    max_num_audio_channels behaviour — it only implements the behaviour above.
"""

import logging

from remove_stereo_if_has_multichannel.lib.ffmpeg import StreamMapper, Probe, Parser
from unmanic.libs.unplugins.settings import PluginSettings

logger = logging.getLogger("Unmanic.Plugin.remove_stereo_if_has_multichannel")

class Settings(PluginSettings):
    settings = {
        "keep_flac_stereo": False
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = { 
            'keep_flac_stereo': {
                'label': 'Keep FLAC Stereo Tracks',
                'type': 'bool'
            }
        }

class PluginStreamMapper(StreamMapper):
    def __init__(self):
        # Only care about audio streams
        super(PluginStreamMapper, self).__init__(logger, ['audio'])
        self.probe = None
        self._streams_to_remove = []

    def set_probe(self, probe):
        self.probe = probe
        try:
            # try to preserve parent's behaviour if present
            super(PluginStreamMapper, self).set_probe(probe)
        except Exception:
            pass

    def _collect_audio_by_language(self):
        """
        Group audio streams by language tag.
        Returns: dict(lang_code -> [stream, ...])
        Language detection checks tags.language, stream['language'], then 'und'.
        """
        streams = self.probe.get_probe()["streams"]
        lang_map = {}

        for s in streams:
            try:
                if s.get('codec_type', '').lower() != 'audio':
                    continue
            except Exception:
                continue

            tags = s.get('tags') or {}
            lang = None
            if isinstance(tags, dict):
                lang = tags.get('language') or tags.get('LANGUAGE')

            if not lang:
                lang = s.get('language')

            if not lang:
                lang = 'und'

            try:
                lang = str(lang)
            except Exception:
                lang = 'und'

            lang_map.setdefault(lang, []).append(s)

        return lang_map

    def streams_need_processing(self):
        """
        Determine which streams (if any) should be removed according to:
          - For each language group, if any stream has channels > 2, mark all
            stereo (2-channel) streams of that language for removal.
        Returns True if there are streams to remove.
        """
        self._streams_to_remove = []
        probe_data = self.probe.get_probe()
        streams = probe_data.get("streams", [])
        if not streams:
            logger.debug("No streams found in probe.")
            return False

        lang_map = self._collect_audio_by_language()

        for lang, s_list in lang_map.items():
            # Helper to safely extract channel count
            def get_channels(stream):
                ch = stream.get('channels')
                if ch is None and 'codec' in stream and isinstance(stream['codec'], dict):
                    ch = stream['codec'].get('channels')
                try:
                    return int(ch)
                except Exception:
                    return 0

            # Check if any track in this language is multichannel
            has_multichannel = any(get_channels(s) > 2 for s in s_list)

            if not has_multichannel:
                continue

            # Mark stereo tracks for removal
            for s in s_list:
                ch = get_channels(s)
                if ch == 2:
                    codec_name = (s.get('codec_name') or '').lower()
                    if self.keep_flac_stereo and codec_name == 'flac':
                        logger.info(f"Keeping FLAC stereo stream (lang='{lang}') due to setting.")
                        continue  # Skip removing FLAC stereo

                    idx = s.get('index') or s.get('id')
                    if idx is None:
                        continue
                    try:
                        idx = int(idx)
                    except Exception:
                        continue

                    if idx not in self._streams_to_remove:
                        logger.info(f"Marking stereo stream #{idx} (lang='{lang}') for removal because a multichannel stream exists.")
                        self._streams_to_remove.append(idx)

        if self._streams_to_remove:
            logger.debug("Streams to remove: {}".format(self._streams_to_remove))
            return True

        logger.debug("No stereo streams require removal.")
        return False

    def test_stream_needs_processing(self, stream_info: dict):
        """
        Called by base StreamMapper to decide per-stream processing.
        Return True only for streams we've scheduled for removal.
        """
        try:
            idx = int(stream_info.get('index'))
        except Exception:
            try:
                idx = int(stream_info.get('id'))
            except Exception:
                return False

        return idx in self._streams_to_remove

    def custom_stream_mapping(self, stream_info: dict, stream_id: int):
        """
        If this stream is scheduled for removal, return an empty mapping to drop it.
        Otherwise return None to allow the base mapper to handle the stream normally.
        """
        if stream_id in self._streams_to_remove:
            logger.debug("Custom mapping: removing stream #{}".format(stream_id))
            return {
                'stream_mapping': [f'-map 0:{stream_id}'],
                'stream_encoding': [f'-c:{stream_id} copy'],
            }
        return None


def on_library_management_file_test(data):
    """
    During library tests mark the file for processing if plugin logic finds
    stereo streams that should be removed.
    """

    abspath = data.get('path')
    probe = Probe(logger, allowed_mimetypes=['video'])
    if not probe.file(abspath):
        return data
         
    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    mapper = PluginStreamMapper()
    mapper.set_probe(probe)
    mapper.set_input_file(abspath)
    mapper.keep_flac = settings.get_setting('keep_flac_stereo')

    if mapper.streams_need_processing():
        data['add_file_to_pending_tasks'] = True
        logger.debug("File '{}' should be added to task list. Probe found stereo streams to remove.".format(abspath))
    else:
        logger.debug("File '{}' does not require processing.".format(abspath))

    del mapper
    return data


def on_worker_process(data):
    """
    When worker runs, generate ffmpeg args to drop the scheduled stereo streams.
    """
    data['exec_command'] = []
    data['repeat'] = False

    abspath = data.get('file_in')
    probe = Probe(logger, allowed_mimetypes=['video'])
    if not probe.file(abspath):
        return data

    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    mapper = PluginStreamMapper()
    mapper.set_probe(probe)
    mapper.set_input_file(abspath)
    mapper.keep_flac = settings.get_setting('keep_flac_stereo')

    if mapper.streams_need_processing():
        mapper.set_output_file(data.get('file_out'))
        ffmpeg_args = mapper.get_ffmpeg_args()
        data['exec_command'] = ['ffmpeg'] + ffmpeg_args

        parser = Parser(logger)
        parser.set_probe(probe)
        data['command_progress_parser'] = parser.parse_progress
        logger.debug("Generated ffmpeg command for '{}': ffmpeg {}".format(abspath, ' '.join(ffmpeg_args)))
    else:
        logger.debug("No ffmpeg command required for '{}'.".format(abspath))

    return data