from unmanic.libs.unplugins.settings import PluginSettings
import subprocess
import json
import os

class Settings(PluginSettings):
    """
    Settings for the Stereo AAC Downmix plugin (Unmanic UI configuration).
    """
    settings = {
        "Encoder": "aac",                # Default AAC encoder
        "Bitrate": "192k",              # Default bitrate
        "MakeStereoDefault": True       # Toggle default-track behavior
    }
    form_settings = {
        "Encoder": {
            "input_type": "select",
            "select_options": [
                {"value": "aac", "label": "Native AAC"},
                {"value": "libfdk_aac", "label": "Fraunhofer FDK AAC"}
            ],
            "label": "AAC Encoder"
        },
        "Bitrate": {
            "input_type": "text",
            "label": "AAC Bitrate (e.g. 192k)"
        },
        "MakeStereoDefault": {
            "input_type": "checkbox",
            "label": "Make stereo AAC default if no FLAC found"
        }
    }

def on_library_management_file_test(data):
    """
    Skip files that do not have any audio stream with >2 channels.
    Uses ffprobe to examine channel count:contentReference[oaicite:4]{index=4}.
    """
    path = data.get('path')
    if not path:
        return
    try:
        # Probe only audio streams for channel count
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'a',
            '-show_entries', 'stream=channels', '-of', 'json', path
        ], capture_output=True, text=True, check=True)
        probe = json.loads(result.stdout)
    except Exception:
        # Skip non-media or unreadable files
        data['add_file_to_pending_tasks'] = False
        data['issues'].append({
            'id': 'audio_stereo_downmix',
            'message': "File is not a valid video/media file (ffprobe failed)."
        })
        return

    streams = probe.get('streams', [])
    # Check for any multi-channel audio (>2 channels)
    has_multich = any((stream.get('channels') or 0) > 2 for stream in streams)
    if not has_multich:
        data['add_file_to_pending_tasks'] = False
    return

def on_worker_process(data):
    """
    Build and execute an ffmpeg command to add a stereo AAC track.
    Uses pan filter for proper downmix:contentReference[oaicite:5]{index=5} and preserves original tracks.
    """
    settings = Settings(library_id=data.get('library_id'))
    encoder = settings.get_setting('Encoder') or 'aac'
    bitrate = settings.get_setting('Bitrate') or '192k'
    make_stereo_default = settings.get_setting('MakeStereoDefault')

    input_file = data.get('file_in')
    output_file = data.get('file_out')

    # Probe streams (index, codec, channels, language) via ffprobe
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries',
            'stream=index,codec_type,codec_name,channels:stream_tags=language',
            '-of', 'json', input_file
        ], capture_output=True, text=True, check=True)
        probe = json.loads(result.stdout)
    except Exception:
        data['worker_log'].append("FFprobe failed; skipping processing.")
        data['exec_command'] = False
        return

    # Identify audio streams
    streams = probe.get('streams', [])
    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    if not audio_streams:
        data['worker_log'].append("No audio streams found; skipping.")
        data['exec_command'] = False
        return

    # Select the first multi-channel audio (>2 channels) to downmix
    multi_streams = [s for s in audio_streams if (s.get('channels') or 0) > 2]
    if not multi_streams:
        data['worker_log'].append("No multi-channel audio stream; skipping.")
        data['exec_command'] = False
        return

    orig_stream = multi_streams[0]
    orig_index = orig_stream['index']
    # Get language tag of original stream (if any)
    lang = None
    if orig_stream.get('tags'):
        lang = orig_stream['tags'].get('language')

    # Check for a FLAC audio stream
    flac_streams = [s for s in audio_streams if s.get('codec_name') == 'flac']
    flac_index = flac_streams[0]['index'] if flac_streams else None

    # Build ffmpeg command
    # Map all streams and add a duplicate of the selected multi-channel audio
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'info',
        '-i', input_file,
        '-map', '0',                        # map all streams
        '-map', f'0:a:{orig_index}',        # add multichannel audio again
        '-c', 'copy'                        # copy all streams by default
    ]

    # New stereo track will be at index = original audio count (0-based)
    new_index = len(audio_streams)

    # Define pan filter coefficients for stereo downmix (includes LFE)
    pan_filter = (
        'pan=stereo|'
        'FL=0.5*FC+0.707*FL+0.707*BL+0.5*LFE|'
        'FR=0.5*FC+0.707*FR+0.707*BR+0.5*LFE'
    )

    # Apply filter and encode the new track
    cmd += [
        f'-filter:a:{new_index}', pan_filter,
        f'-c:a:{new_index}', encoder,
        f'-b:a:{new_index}', bitrate,
        f'-metadata:s:a:{new_index}', 'title=Stereo AAC (Downmix)'
    ]
    if lang:
        cmd += [f'-metadata:s:a:{new_index}', f'language={lang}']

    # Set default track dispositions
    if flac_index is not None:
        # Make FLAC the default, unset original
        cmd += [f'-disposition:a:{flac_index}', 'default']
        cmd += [f'-disposition:a:{orig_index}', '0']
    else:
        if make_stereo_default:
            # No FLAC: make new stereo default, unset original
            cmd += [f'-disposition:a:{new_index}', 'default']
            cmd += [f'-disposition:a:{orig_index}', '0']
        # else leave original defaults unchanged

    # Safeguard against muxing queue overflow
    cmd += ['-max_muxing_queue_size', '9999', output_file]

    data['worker_log'].append(f"Executing ffmpeg command: {' '.join(cmd)}")
    data['exec_command'] = cmd
    return
