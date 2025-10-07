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
        streams = self.probe.get_probe()["streams"]
        if not streams:
            logger.debug("No streams found in probe.")
            return False

        lang_map = self._collect_audio_by_language()

        for lang, s_list in lang_map.items():
            # Check for presence of mulitchannel (>2) in this language
            has_multichannel = False
            for s in s_list:
                try:
                    ch = int(s.get('channels') or 0)
                    if ch > 2:
                        has_multichannel = True
                        break
                except Exception:
                    continue

            if not has_multichannel:
                # nothing to do for this language
                continue

            # mark stereo tracks (==2 channels) for removal
            for s in s_list:
                try:
                    ch = int(s.get('channels') or 0)
                except Exception:
                    continue
                if ch == 2:
                    codec_name = ''

                    try:
                        codec_field = s.get('codec_name') or s.get('codec') or s.get('codec_tag_string') or ''
                        # codec_field may be dict in some probes, handle that
                        if isinstance(codec_field, dict):
                            codec_name = (codec_field.get('name') or codec_field.get('codec_name') or '').lower()
                        else:
                            codec_name = str(codec_field).lower()
                    except Exception:
                        codec_name = ''

                    if self.keep_flac_stereo and codec_name == 'flac':
                        logger.info("Keeping FLAC stereo stream (lang='{}', stream={}) due to keep_flac_stereo setting.".format(lang, s))
                        continue  # Skip removing FLAC stereo

                    idx = s.get('index')
                    if idx is None:
                        idx = s.get('id')
                    if idx is None:
                        # try to derive index from stream dict keys (unlikely)
                        continue
                    idx = int(idx)
                    if idx not in self._streams_to_remove:
                        logger.info(
                            "Marking stereo stream #{} (lang='{}') for removal because a multichannel stream exists.".format(idx, lang)
                        )
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

    def get_ffmpeg_args(self):
        """
        Generate ffmpeg args that explicitly map and copy all streams
        except those marked for removal.
        """
        if not self.probe:
            logger.error("get_ffmpeg_args() called before probe is set.")
            return []

        logger.debug("Using custom get_ffmpeg_args() with explicit stream mapping.")

        probe_data = self.probe.get_probe()
        streams = probe_data.get('streams', [])

        if not streams:
            logger.warning("No streams found in probe, nothing to map.")
            return []

        args = []
        for s in streams:
            idx = s.get('index')
            if idx is None:
                idx = s.get('id')
            if idx is None:
                continue

            try:
                idx = int(idx)
            except Exception:
                try:
                    idx = int(str(idx).split(':')[-1])
                except Exception:
                    continue

            if idx in self._streams_to_remove:
                logger.debug(f"Skipping stereo stream #{idx} (to be removed)")
                continue

            # Map all other streams
            args += ['-map', f'0:{idx}']
            logger.debug(f"Mapping stream #{idx}")

        # Copy all remaining streams without re-encoding
        args += ['-c', 'copy']

        return args

def on_library_management_file_test(data):
    """
    During library tests mark the file for processing if plugin logic finds
    stereo streams that should be removed.
    """

    abspath = data.get('path')
    probe = Probe(logger, allowed_mimetypes=['video'])
    if not probe.file(abspath):
        return data

    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    mapper = PluginStreamMapper()
    mapper.set_probe(probe)
    mapper.set_input_file(abspath)
    mapper.keep_flac_stereo = bool(settings.get_setting('keep_flac_stereo'))

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

    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    mapper = PluginStreamMapper()
    mapper.set_probe(probe)
    mapper.set_input_file(abspath)
    mapper.keep_flac_stereo = bool(settings.get_setting('keep_flac_stereo'))

    if mapper.streams_need_processing():
        mapper.set_output_file(data.get('file_out'))
        
        ffmpeg_args = [
            '-hide_banner', '-loglevel', 'info',
            '-i', abspath,
            '-strict', '-2', '-max_muxing_queue_size', '4096'
        ]

        ffmpeg_args += mapper.get_ffmpeg_args()

        ffmpeg_args += ['-y', data.get('file_out')]

        data['exec_command'] = ['ffmpeg'] + ffmpeg_args

        parser = Parser(logger)
        parser.set_probe(probe)
        data['command_progress_parser'] = parser.parse_progress
        logger.debug("Generated ffmpeg command for '{}': ffmpeg {}".format(abspath, ' '.join(ffmpeg_args)))
    else:
        logger.debug("No ffmpeg command required for '{}'.".format(abspath))

    return data