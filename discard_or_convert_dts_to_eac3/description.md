
---

##### Links:

- [Support](https://unmanic.app/discord)

---

##### Description:

- This plugin handles unwanted DTS audio tracks based on a set of rules...
    - If no other surround tracks exist, the DTS track is converted to EAC3 at the bitrate specified
    - If another surround track exists, and its bitrate is higher than the specified threshold bitrate, the DTS track is discarded in favor of it. If the bitrate is below the specified threshold, the surround track is discarded and the DTS track is converted to EAC3 at the specified bitrate
---

##### Documentation:

For information on the available encoder settings:
- [FFmpeg - High Quality Audio Encoding](https://trac.ffmpeg.org/wiki/Encode/HighQualityAudio)
--- 

##### Config description:

bit_rate - set the aggregate bit rate for all audio streams.  The default setting is 640k

threshold_bit_rate - The bitrate for non-dts surround tracks to be kept

