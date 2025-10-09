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

import logging, os, shlex

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
        self.abspath = None

    def set_default_values(self, settings, abspath, probe):
        """
        Configure the stream mapper with defaults
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
            br = stream_info.get('bit_rate') or None
            if br:
                try:
                    br_int = int(br)
                    kbps = int(round(br_int / 1000.0))
                    return kbps
                except Exception:
                    pass
            # fallback to tags (sometimes present)
            tags = stream_info.get('tags') or {}
            tag_br = tags.get('BPS') or tags.get('BITRATE') or tags.get('bitrate')
            if tag_br:
                if isinstance(tag_br, (int, float)):
                    return int(round(float(tag_br) / 1000.0))
                if isinstance(tag_br, str):
                    if tag_br.lower().endswith('k'):
                        try:
                            return int(tag_br[:-1])
                        except Exception:
                            pass
                    try:
                        return int(round(int(tag_br) / 1000.0))
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
        """
        codec = (stream_info.get('codec_name') or '').lower()
        channels = int(stream_info.get('channels', 2))

        # if stream is already AAC and stereo (or mono) it doesn't need downmixing
        if codec == self.codec and channels <= 2:
            return False

        # Only add downmix tracks for multichannel source streams
        if channels <= 2:
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
                    logger.debug("Skipping downmix for language '%s' because a stereo/mono stream already exists.", this_lang)
                    return False

        logger.debug("Stream (lang=%s, channels=%d) requires a downmixed stereo to be added.", this_lang, channels)
        return True

    def _collect_downmix_candidates(self):
        """
        Return a list of tuples (input_audio_index, stream_info) that should get a downmix.
        """
        candidates = []
        streams = self._get_streams_from_probe()
        for idx, s in enumerate(streams):
            if (s.get('codec_type') or '').lower() != 'audio':
                continue
            try:
                channels = int(s.get('channels', 2))
            except Exception:
                channels = 2
            if channels > 2 and self.test_stream_needs_processing(s):
                candidates.append((idx, s))
        return candidates

    def build_ffmpeg_command(self, infile: str, outfile: str):
        """
        Build a complete ffmpeg command that:
        - copies all video/subtitle streams,
        - copies original audio streams (preserve multichannel originals),
        - appends one downmixed stereo AAC track per candidate multichannel stream,
          with language metadata and disposition default, and with chosen bitrate.

        Returns: list suitable for exec (['ffmpeg', ...])
        """
        probe_streams = self._get_streams_from_probe()

        # start command
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-y', '-i', infile]

        # We'll map video and subtitle streams explicitly (copy them)
        # and then map each audio stream (copy). After that we map downmix outputs we generate with -filter_complex.
        map_args = []
        codec_args = []
        filter_parts = []
        downmix_maps = []
        audio_output_counter = 0  # output audio stream index counter (increments as we add -map for audios)

        # Map video streams
        for idx, s in enumerate(probe_streams):
            if (s.get('codec_type') or '').lower() == 'video':
                map_args += ['-map', f'0:v:{idx}']
        # Map subtitle streams (if any)
        for idx, s in enumerate(probe_streams):
            if (s.get('codec_type') or '').lower() in ('subtitle', 'subtitles', 'text'):
                map_args += ['-map', f'0:s:{idx}']

        # Map original audio streams as copies (preserve originals)
        audio_input_order = []
        for idx, s in enumerate(probe_streams):
            if (s.get('codec_type') or '').lower() == 'audio':
                map_args += ['-map', f'0:a:{idx}']
                # set copy for this output audio index
                codec_args += ['-c:a:{}'.format(audio_output_counter), 'copy']
                audio_input_order.append((idx, s))
                audio_output_counter += 1

        # Determine downmix candidates (input index, stream_info)
        downmix_candidates = self._collect_downmix_candidates()

        # Build filter_complex pieces for each downmix candidate and map them
        dm_count = 0
        for (in_idx, sinfo) in downmix_candidates:
            # language of this stream
            lang = self._stream_language(sinfo)
            # formula to use
            formula = self.settings.get_setting('formula') or ''
            # The formula can include characters that need not be escaped here; ffmpeg accepts the filter string as is.
            # Create a label for this downmix
            dm_label = f"dm{dm_count}"
            # Build filter part: [0:a:in_idx] <formula> [dm_label]
            # If user formula already includes an output label or a chain, they must be compatible. We assume it's a single filter (pan=...).
            filter_parts.append(f"[0:a:{in_idx}]{formula}[{dm_label}]")
            # Map the produced label as an output audio stream
            downmix_maps += ['-map', f'[{dm_label}]']
            # determine bitrate
            target_kbps = 0
            try:
                target_kbps = int(self.settings.get_setting('target_bitrate_kbps') or 0)
            except Exception:
                target_kbps = 0
            source_kbps = self._get_stream_bitrate_kbps(sinfo)
            if source_kbps is not None and source_kbps > 0 and target_kbps > 0:
                chosen_kbps = source_kbps if source_kbps < target_kbps else target_kbps
            elif target_kbps > 0:
                chosen_kbps = target_kbps
            elif source_kbps is not None:
                chosen_kbps = source_kbps
            else:
                chosen_kbps = 256
            bitrate_arg = f"{int(chosen_kbps)}k"

            # For this appended downmix, set codec= aac and bitrate - the output audio index is current audio_output_counter
            codec_args += ['-c:a:{}'.format(audio_output_counter), self.encoder]
            codec_args += ['-b:a:{}'.format(audio_output_counter), bitrate_arg]

            # add metadata + disposition for this output audio index (use language tag as-is)
            codec_args += ['-metadata:s:a:{}'.format(audio_output_counter), f"language={lang}"]
            codec_args += ['-disposition:a:{}'.format(audio_output_counter), 'default']

            audio_output_counter += 1
            dm_count += 1

        # If we created filters, combine them and add -filter_complex
        if filter_parts:
            filter_complex_str = ';'.join(filter_parts)
            args += ['-filter_complex', filter_complex_str]
            # append the downmix maps after filter_complex
            map_args += downmix_maps

        # Now append mapping args
        args += map_args

        # Add codec args for video/subs (copy)
        args += ['-c:v', 'copy']
        # copy subtitle streams if present
        args += ['-c:s', 'copy']
        # Add codec args assembled earlier (audio copy + downmixed enc args)
        args += codec_args

        # Add outfile
        args += [outfile]

        # Final command ready
        logger.debug("Built ffmpeg command: %s", ' '.join(map(shlex.quote, args)))
        return args

    # Keep old custom_stream_mapping in case other code needs it; but we won't use it for the worker command
    def custom_stream_mapping(self, stream_info: dict, stream_id: int):
        """
        Legacy single-stream mapping pathway (not used by the new build_ffmpeg_command()).
        We keep it for compatibility, but the worker will use build_ffmpeg_command() which preserves originals.
        """
        stream_encoding = ['-c:a:{}'.format(stream_id), self.encoder]
        formula = self.settings.get_setting('formula') or ''
        custom_options = '-filter:a:{} '.format(stream_id) + formula
        stream_encoding += custom_options.split()
        lang = self._stream_language(stream_info)
        stream_encoding += ['-metadata:s:a:{}'.format(stream_id), 'language={}'.format(lang)]
        stream_encoding += ['-disposition:a:{}'.format(stream_id), 'default']
        # bitrate logic (best-effort)
        target_kbps = 0
        try:
            target_kbps = int(self.settings.get_setting('target_bitrate_kbps') or 0)
        except Exception:
            target_kbps = 0
        source_kbps = self._get_stream_bitrate_kbps(stream_info)
        if source_kbps is not None and source_kbps > 0 and target_kbps > 0:
            chosen_kbps = source_kbps if source_kbps < target_kbps else target_kbps
        elif target_kbps > 0:
            chosen_kbps = target_kbps
        elif source_kbps is not None:
            chosen_kbps = source_kbps
        else:
            chosen_kbps = 256
        bitrate_arg = '{}k'.format(int(chosen_kbps))
        stream_encoding += ['-b:a:{}'.format(stream_id), bitrate_arg]

        return {
            'stream_mapping':  ['-map', '0:a:{}'.format(stream_id)],
            'stream_encoding': stream_encoding,
        }


def on_library_management_file_test(data):
    """
    Decide if file needs processing
    """
    abspath = data.get('path')
    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(abspath):
        return data

    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    mapper = PluginStreamMapper()
    mapper.set_default_values(settings, abspath, probe)

    if mapper.streams_need_processing():
        data['add_file_to_pending_tasks'] = True
        logger.debug("File '{}' should be added to task list. Probe found streams require processing.".format(abspath))
    else:
        logger.debug("File '{}' does not contain streams require processing.".format(abspath))

    return data


def on_worker_process(data):
    """
    Build and apply ffmpeg command using mapper.build_ffmpeg_command() to
    preserve originals and append downmixed AAC stereo tracks.
    """
    data['exec_command'] = []
    data['repeat'] = False

    infile = data.get('file_in')
    outfile = data.get('file_out')

    probe = Probe(logger, allowed_mimetypes=['audio', 'video'])
    if not probe.file(infile):
        return data

    settings = Settings(library_id=data.get('library_id'))
    mapper = PluginStreamMapper()
    mapper.set_default_values(settings, infile, probe)

    if mapper.streams_need_processing():
        # Build full ffmpeg command which preserves originals and adds downmix tracks
        ffmpeg_args = mapper.build_ffmpeg_command(infile, outfile)

        data['exec_command'] = ffmpeg_args

        parser = Parser(logger)
        parser.set_probe(probe)
        data['command_progress_parser'] = parser.parse_progress

    return data
