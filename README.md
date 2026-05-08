# Stitched Text Video

Generates a 1080x1920 video by stitching random clips from `videos/` until a randomly chosen length between 8 and 18 seconds is reached. Each selected clip segment is at least 3 seconds long. The final render overlays user-entered text in white Crimson Text with no stroke, then adds one random track from `music/` clipped to the video length.

## Run

```bash
python3 generate_video.py
```

The script will ask:

```text
Text to overlay:
```

You can also pass text directly:

```bash
python3 generate_video.py --text "your text here"
```

Outputs are written to `outputs/`.
