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

On a Mac, double-click `Sparkz Creator.app` or `Run App.command` in the project folder. The command creates a virtualenv if needed, installs Python and frontend dependencies, and opens the app. Keep that window open while you use it, and press Control-C there to stop it.

Startup writes a line for each step, including the Python dependency check, which can sit for a while on a cold start before anything listens. `Sparkz Creator.app` keeps waiting while that log is still growing (up to 10 minutes) and only reports failure if the log goes quiet for a minute or the launcher exits. The log is `~/Library/Logs/Sparkz Creator.log`. Opening the app again while those servers are already up opens the existing app instead of stopping on a port conflict. A previous Sparkz Creator that is still holding the ports but not responding is stopped and started again. Another program on port 8000 or 5173 is left alone and named in the log.

The app bundle is built from `macos/SparkzCreatorLauncher.swift`. Rebuild it after a launcher change:

```bash
mkdir -p "Sparkz Creator.app/Contents/MacOS" "Sparkz Creator.app/Contents/Resources"
cp macos/Info.plist "Sparkz Creator.app/Contents/Info.plist"
cp macos/SparkzCreator.icns "Sparkz Creator.app/Contents/Resources/SparkzCreator.icns"
swiftc -O -framework AppKit \
  -o "Sparkz Creator.app/Contents/MacOS/SparkzCreatorLauncher" \
  macos/SparkzCreatorLauncher.swift
chmod +x "Run App.command"
```

You can also start the two processes yourself:

```bash
python3 -m pip install -r requirements.txt
cd frontend && npm install
# terminal 1, from the project folder
python3 -m uvicorn web_app:app --host 127.0.0.1 --port 8000
# terminal 2
cd frontend && npm run dev
```

ffmpeg is installed with the Python dependencies through `imageio-ffmpeg`, so a separate ffmpeg install is not required. Text Slideshow Reels prefers `FFMPEG_PATH` or `ffmpeg` on `PATH` when that build has libx264 and AAC, and otherwise uses the same bundled binary.

To add another tool, append it to `frontend/src/tools.js` with a name, description, icon, route, and component. Home and the router both read that list. The component receives `{ onHome }` so it can return to Home.

### Timeline Video Editor

Choose a format, pick a clip folder, select clips in order, enter text, choose music, render the video, and download outputs from `outputs/`.

### Fast Cut Slideshow

Open Home → Fast Cut Slideshow, or go to `http://127.0.0.1:5173/tools/slideshow`.

Drop in photos (or choose several at once), drag or use the arrows to reorder them, and shuffle if you want. Type the overlay text, with a line break between lines. Each photo holds for 0.417 seconds by default — 10 frames at 24fps, a hard cut, full-bleed and center-cropped, with no zoom. Change the seconds-per-photo value to hold them longer or shorter; the renderer snaps that value to a whole frame. The video length is the photo count times that hold, even when there are more photos than the music.

The text is centered horizontally and sits at the same vertical center as the 960×576 reference. It is drawn in TikTok Sans SemiBold at TikTok's yellow swatch `#F2CD46`, with no outline or shadow, and the size scales with the frame width so it covers the same share of the picture. TikTok's in-app Classic style is Proxima Nova Semibold, which is commercial and not bundled. TikTok Sans is the SIL Open Font License face in `fonts/TikTokSans/`.

Music is optional. Leave the picker on Random to use a track from `music/` (trimmed to the video), choose No music, or upload a track. Put files in `music/` yourself if you already have them. Render, preview the MP4, and download it from `outputs/`. The export is always 1080×1920 at 24fps. **Photo cut format** chooses the shape of the photos inside that frame. **16:9** (the default) is a 1080×608 band centered with black above and below. **1:1** is a centered 1080×1080 square. **9:16** fills the frame. Photos are center-cropped into that shape. The hook text stays inside the photo area.

**End demo video** is optional. Upload an MP4 or MOV and it plays in full after the last photo, scaled and center-cropped to fill the 1080×1920 frame with no black bars. The hook text stays on the photo cuts only and disappears when the demo starts. The length is the photo count times the per-photo hold, plus the demo. The last demo you upload is stored in `uploads/demo/` and stays selected until you remove it, so the next renders can reuse it. **Keep demo video's own audio** is off by default. Leave it off and only the music track is heard; that track keeps playing under the demo and is trimmed, with a short fade, at the end. Turn it on to mix the demo's audio in during the demo. A demo file with no audio stream still renders.

The same option is on the command line. Pass one folder of photos, or a folder of those folders. Each set can include `hook.txt` (or `text.txt`) for its own overlay. `--demo` is shared by every set:

```bash
python3 slideshow.py path/to/photos --text "hook line" --demo path/to/demo.mp4
python3 slideshow.py path/to/batch --demo path/to/demo.mp4 --format 1:1
python3 slideshow.py path/to/one-set --demo path/to/demo.mp4 --format 9:16 --keep-demo-audio --music song.mp3 --output out.mp4
```

### Text Slideshow

Open Home → Text Slideshow, or go to `http://127.0.0.1:5173/tools/text-slideshow`.

Each slide is 1080×1920, for a TikTok photo carousel or an Instagram Reel. A slide has a background photo (cover-cropped, no dimming) or a two-color gradient, any number of text boxes, and any number of image overlays.

Text is TikTok Sans. The weight menu lists the files in `fonts/TikTokSans/` (Regular, Medium, SemiBold, Bold). **Add title** drops a box in the reference heading style: SemiBold, 53px, `#000000`, centered, 820px wide, starting at the measured vertical position. **Add body text** uses Medium at 43px with a line pitch of 51px, so a blank line in the text is the paragraph gap from the reference. **Add label** turns on the black rounded highlight (12px corners, white text, Medium 41px) used for the “se llama …” tag. Drag a box to move it. Drag the side handles to change the wrap width. Image overlays keep their aspect ratio unless you unlock it, and they default to a 16px corner radius like the reference screenshot. Bring forward and send back change the layer order.

The on-screen preview is a PNG from the same Pillow renderer as the export, so what you see is what downloads. **Download PNGs** writes `01.png`, `02.png`, … into `outputs/<timestamp>_textslides/` and downloads a zip of those files. That export is unchanged.

**Download Reel** uses those same frames to make `video.mp4` in that output folder and downloads it. The file is 1080×1920 H.264 (`yuv420p`, High profile, Rec. 709), 30fps, with AAC audio and the moov atom at the front, so Instagram Reels can play it. Each slide stays up for the seconds you set (default 2.75). Turn on **Hold the first slide longer** when the cover should sit a bit longer than the rest. The default join is a hard cut. Choose **Crossfade** for a short dissolve (default 0.35s, at most 1.5s, and always shorter than a slide). Add an audio file if you want music: a shorter track loops, a longer one is trimmed, and the end fades out over about half a second. Leave audio empty and the video still has a silent AAC track.

**Music from URL** uses the same importer as the original music tab. Paste an Instagram reel or video URL, then **Fetch MP3**. The app downloads the video, converts the audio to MP3, and saves it in `music/` so you can reuse it. Turn on **Use Chrome login for restricted posts** when Instagram blocks anonymous access. **Saved tracks** lists everything already in `music/`, including MP3s fetched earlier. Pick one to preview it and to use it as this Reel's background. A file you add from disk is used instead of the saved-track pick. Either way the track is looped or trimmed to the video and fades out at the end.

The demo deck is English placeholder copy in that measured layout, so you can try it before uploading photos.

### Text Slideshow batch export

The same renderer runs from the command line, for one deck or a folder of them:

```bash
python3 text_slideshow_video.py path/to/decks
python3 text_slideshow_video.py path/to/one-deck --seconds 2.75 --first-seconds 4
python3 text_slideshow_video.py path/to/one-deck --audio song.mp3 --fade 0.35
python3 text_slideshow_video.py path/to/decks --audio-url "https://www.instagram.com/reel/XXXX/"
python3 text_slideshow_video.py path/to/one-deck --audio-url "https://www.instagram.com/reel/XXXX/" --chrome-login
```

A deck is either:

- a folder with `deck.json` (and the images it names), or
- a folder of numbered PNG slides, such as `01.png`, `02.png`, from an earlier export.

A directory of those decks is a batch. JSON decks write `output/video.mp4` plus the numbered PNGs. A folder that is already PNG slides gets `video.mp4` next to them. `--output` sets the mp4 path for a single deck.

`deck.json` uses the same slide objects as the export API:

```json
{
  "slides": [
    {
      "background_color": "#f4d5c4",
      "background_color_2": "#d7e6f6",
      "background_asset_id": null,
      "elements": [
        {
          "type": "text",
          "text": "how to be a better partner",
          "x": 130,
          "y": 352,
          "width": 820,
          "font_weight": "medium",
          "font_size": 66,
          "color": "#000000",
          "align": "center",
          "line_height": 1.186
        }
      ]
    }
  ],
  "assets": {},
  "seconds_per_slide": 2.75,
  "first_slide_seconds": 4,
  "crossfade_seconds": 0,
  "audio": "song.mp3"
}
```

`assets` maps an id used by `background_asset_id` or an image element to a path relative to the deck folder. If an id is omitted, the exporter also looks for `assets/<id>.png`. `audio` is optional and relative to the deck. Omit `first_slide_seconds` to hold every slide for the same length. `crossfade_seconds: 0` is a hard cut.

`--audio-url` calls the same Instagram importer as **Music from URL** in the editor (`import_instagram_music`). It accepts an Instagram reel or video URL, writes an MP3 into `music/`, and uses that file for every deck in the batch. `--chrome-login` reads the Chrome login for a post Instagram will not serve anonymously. Pass `--audio` or `--audio-url`, not both. The saved track is still looped or trimmed to the video, with the same short fade out.

`POST /api/text-slideshow/export-video` is the same render, as multipart form fields: `slides` (JSON), `seconds_per_slide`, `first_slide_seconds`, `crossfade_seconds`, and an optional `audio` file or `music_name` from `music/`. Fetching a URL is the existing `POST /api/music/import` (body: `url`, optional `use_browser_cookies`). The editor fetches first, then exports with the saved `music_name`. The response is the mp4. PNG export stays on `POST /api/text-slideshow/export`.

ffmpeg is detected in this order: `FFMPEG_PATH`, then `ffmpeg` on `PATH`, then the binary bundled with `imageio-ffmpeg`. `python3 -m pip install -r requirements.txt` installs that bundle, which is also what `Run App.command` installs, so a separate ffmpeg install is optional. The binary needs libx264 and AAC. On a Mac, `brew install ffmpeg` is enough if you want the system one to win.
