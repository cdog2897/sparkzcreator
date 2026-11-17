# Stitched Text Video

Generates text-overlay videos from the structured `videos/` folder layout:

```text
videos/
  Horizontal/
    Name_Random/
    Name_Ordered/
    Name_Single/
  Square/
    Name_Random/
    Name_Ordered/
    Name_Single/
  Vertical/
    Name_Random/
    Name_Ordered/
    Name_Single/
```

The suffix on each clip folder controls how clips are used:

- `_Random`: choose unique clips in random order and trim montage-style segments up to the selected music length.
- `_Ordered`: use every clip in natural filename order, trimming each clip to varied durations when the selected music is shorter than the full folder.
- `_Single`: ask which one clip to use.

If Random or Ordered footage is shorter than the selected music track, the output ends when the footage ends and the music is trimmed. In Single mode, the output length is always the shorter of the selected clip and selected music track.

## Run

```bash
python3 generate_video.py
```

The script is interactive. It asks for:

```text
Format to use:
Folder to generate from:
Clip to use:                 # only for *_Single folders
Text to overlay:
Text size (press Enter for Standard):
Music file to use (press Enter for random):
Clip count option:           # only for *_Random folders
```

Text size options are `Smaller` (`48px`), `Small` (`54px`), `Standard` (`60px`), `Big` (`72px`), and `Bigger` (`84px`). Press Enter to keep `Standard`.

The music picker lists each track with its duration, for example `song-name.mp3 (7.26s)`.

For `_Random` folders, the script lists clip-count options after music is selected. Each option shows the number of clips and average duration per clip. Press Enter to keep the default random assignment.

## Formats

1. `1080x1920` vertical output using `Square` clips expanded to the left and right edges and centered vertically with black top and bottom space. Text is centered on the screen.
2. Same as format 1, but text sits in the black space just above the square clips with a little padding.
3. `1920x1080` horizontal output using `Horizontal` clips filling the screen. Text is centered on the screen.
4. `1080x1920` vertical output using `Vertical` clips filling the screen. Text is centered on the screen.

Outputs are written to `outputs/` with names like:

```text
format2_Field1_Random_20260616_143000.mp4
```

The render uses Crimson Text from `Crimson_Text/`, H.264 video, and AAC audio.

## Web App

The FastAPI + React/Vite app turns the same renderer into an interactive short-form video creator.

Install Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm install
```

Run the API:

```bash
uvicorn web_app:app --reload
```

Run the Vite dev server in another terminal:

```bash
cd frontend
npm run dev
```

Open the Vite URL, usually `http://127.0.0.1:5173`. The app opens on a Home screen. Home lists every tool in `frontend/src/tools.js`. A direct link such as `http://127.0.0.1:5173/tools/timeline` opens that tool, including after a refresh.

On a Mac, double-click `Run App.command` in the project folder. It creates a virtualenv if needed, installs Python and frontend dependencies, and opens the app. Keep that window open while you use it, and press Control-C there to stop it. You can also start the two processes yourself:

```bash
python3 -m pip install -r requirements.txt
cd frontend && npm install
# terminal 1, from the project folder
python3 -m uvicorn web_app:app --host 127.0.0.1 --port 8000
# terminal 2
cd frontend && npm run dev
```

ffmpeg is installed with the Python dependencies through `imageio-ffmpeg`, so a separate ffmpeg install is not required.

To add another tool, append it to `frontend/src/tools.js` with a name, description, icon, route, and component. Home and the router both read that list. The component receives `{ onHome }` so it can return to Home.

### Timeline Video Editor

Choose a format, pick a clip folder, select clips in order, enter text, choose music, render the video, and download outputs from `outputs/`.

### Fast Cut Slideshow

Open Home → Fast Cut Slideshow, or go to `http://127.0.0.1:5173/tools/slideshow`.

Drop in photos (or choose several at once), drag or use the arrows to reorder them, and shuffle if you want. Type the overlay text, with a line break between lines. Each photo holds for 0.417 seconds by default — 10 frames at 24fps, a hard cut, full-bleed and center-cropped, with no zoom. Change the seconds-per-photo value to hold them longer or shorter; the renderer snaps that value to a whole frame. The video length is the photo count times that hold, even when there are more photos than the music.

The text is centered, static, and drawn in TikTok Sans SemiBold at TikTok's yellow swatch `#F2CD46`, with no outline or shadow. TikTok's in-app Classic style is Proxima Nova Semibold, which is commercial and not bundled. TikTok Sans is the SIL Open Font License face in `fonts/TikTokSans/`.

Music is optional. Leave the picker on Random to use a track from `music/` (trimmed to the video), choose No music, or upload a track. Put files in `music/` yourself if you already have them. Render, preview the MP4, and download it from `outputs/`. The default canvas is 1080×1920.
