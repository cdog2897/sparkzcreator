import unittest
from unittest.mock import patch

from generate_video import CTA_TEXT, SourceVideo
from web_app import (
    ClipSelection,
    CoverRenderRequest,
    MusicImportRequest,
    RenderRequest,
    VIDEO_DIR,
    instagram_download_options,
    instagram_import_error,
    is_instagram_url,
    music_library_payload,
    safe_music_filename,
    serialize_clip_path,
    selected_videos_from_references,
)


class WebAppHelperTests(unittest.TestCase):
    def test_instagram_url_validation_accepts_instagram_hosts(self) -> None:
        self.assertTrue(is_instagram_url("https://www.instagram.com/reel/example/"))
        self.assertTrue(is_instagram_url("https://instagram.com/p/example/"))

    def test_instagram_url_validation_rejects_other_hosts(self) -> None:
        self.assertFalse(is_instagram_url("https://example.com/reel/example/"))
        self.assertFalse(is_instagram_url("not-a-url"))

    def test_instagram_import_defaults_to_anonymous_access(self) -> None:
        request = MusicImportRequest(url="https://www.instagram.com/reel/example/")
        options = instagram_download_options(VIDEO_DIR / "download.%(ext)s")

        self.assertFalse(request.use_browser_cookies)
        self.assertNotIn("cookiesfrombrowser", options)

    def test_instagram_import_can_use_chrome_login(self) -> None:
        options = instagram_download_options(
            VIDEO_DIR / "download.%(ext)s",
            browser="chrome",
        )

        self.assertEqual(
            options["cookiesfrombrowser"],
            ("chrome", None, None, None),
        )

    def test_instagram_empty_response_suggests_login(self) -> None:
        message = instagram_import_error(
            RuntimeError("Instagram sent an empty media response")
        )

        self.assertIn("Use Chrome login", message)

    def test_safe_music_filename_sanitizes_uploaded_names(self) -> None:
        self.assertEqual(safe_music_filename("../My Song!.MP3"), "My_Song.mp3")

    def test_safe_music_filename_rejects_non_audio_extensions(self) -> None:
        with self.assertRaises(ValueError):
            safe_music_filename("cover.txt")

    def test_music_library_payload_allows_empty_library(self) -> None:
        with patch("web_app.list_music_tracks", side_effect=FileNotFoundError("empty")):
            self.assertEqual(music_library_payload()["music"], [])

    def test_media_pool_clip_listing_does_not_require_eager_metadata(self) -> None:
        clip = serialize_clip_path(
            VIDEO_DIR / "Folder",
            VIDEO_DIR / "Folder" / "clip.mov",
        )

        self.assertEqual(clip["name"], "clip.mov")
        self.assertEqual(clip["duration"], 0.0)
        self.assertIsNone(clip["width"])
        self.assertIsNone(clip["height"])
        self.assertNotIn("trimPreviewUrl", clip)

    def test_render_request_accepts_cta_flag(self) -> None:
        request = RenderRequest(
            format_option=1,
            add_cta=True,
            cta_text="Plan your next date night!",
        )

        self.assertTrue(request.add_cta)
        self.assertEqual(request.cta_text, "Plan your next date night!")

    def test_render_request_prefills_current_cta_text(self) -> None:
        request = RenderRequest(format_option=1)

        self.assertEqual(request.cta_text, CTA_TEXT)

    def test_render_request_allows_no_music(self) -> None:
        request = RenderRequest(format_option=1)

        self.assertIsNone(request.music_name)

    def test_clip_selection_accepts_explicit_trim_range(self) -> None:
        selection = ClipSelection(
            folder_path="Field",
            clip_name="clip.mov",
            trim_start_seconds=1.25,
            trim_end_seconds=3.5,
        )

        self.assertEqual(selection.trim_start_seconds, 1.25)
        self.assertEqual(selection.trim_end_seconds, 3.5)

    def test_duplicate_clip_references_keep_independent_trim_ranges(self) -> None:
        references = [
            ClipSelection(
                folder_path="Field",
                clip_name="clip.mov",
                trim_start_seconds=0.25,
                trim_end_seconds=1.5,
            ),
            ClipSelection(
                folder_path="Field",
                clip_name="clip.mov",
                trim_start_seconds=2.0,
                trim_end_seconds=3.75,
            ),
        ]

        with patch("web_app.folder_for_media_path", return_value=VIDEO_DIR / "Field"):
            with patch(
                "web_app.list_video_paths",
                return_value=[VIDEO_DIR / "Field" / "clip.mov"],
            ):
                with patch(
                    "web_app.probe_video",
                    return_value=SourceVideo(
                        VIDEO_DIR / "Field" / "clip.mov",
                        5.0,
                    ),
                ):
                    selected = selected_videos_from_references(references)

        self.assertEqual(len(selected), 2)
        self.assertEqual(
            [(video.trim_start_seconds, video.trim_end_seconds) for video in selected],
            [(0.25, 1.5), (2.0, 3.75)],
        )

    def test_cover_render_request_is_separate_from_video_render(self) -> None:
        request = CoverRenderRequest(
            clip=ClipSelection(
                folder_path="Field",
                clip_name="clip.mov",
                crop_x_percent=70,
            ),
            time_seconds=1.25,
        )

        self.assertEqual(request.clip.clip_name, "clip.mov")
        self.assertEqual(request.clip.crop_x_percent, 70)
        self.assertEqual(request.time_seconds, 1.25)
        self.assertNotIn("cover_image", RenderRequest.model_fields)


if __name__ == "__main__":
    unittest.main()
