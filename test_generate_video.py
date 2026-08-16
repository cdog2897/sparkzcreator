import random
import unittest
from pathlib import Path
from unittest.mock import patch

from generate_video import (
    ask_for_random_clip_count,
    ask_for_music_track,
    ClipSegment,
    ClipScaleMode,
    CTA_APPSTORE_PADDING,
    create_cta_overlay_image,
    create_cover_image,
    COVER_IMAGE_FORMAT,
    final_output_duration,
    FORMATS_BY_OPTION,
    TEXT_FONT_SIZE,
    FolderMode,
    SourceVideo,
    format_duration,
    make_segment,
    make_cover_image_filter,
    make_video_filter,
    natural_sort_key,
    overlay_text_and_music,
    parse_folder_mode,
    plan_ordered_segments,
    plan_random_segments,
    plan_selected_segments,
    plan_single_segment,
    prepare_text,
    random_clip_count_options,
    source_root_for_format,
    text_y_expression,
    text_size_for_choice,
)


class GenerateVideoHelperTests(unittest.TestCase):
    def test_parse_folder_mode_from_suffix(self) -> None:
        self.assertIs(parse_folder_mode(Path("Field1_Random")), FolderMode.RANDOM)
        self.assertIs(parse_folder_mode(Path("Truck1_Ordered")), FolderMode.ORDERED)
        self.assertIs(parse_folder_mode(Path("Forest1_Single")), FolderMode.SINGLE)

    def test_parse_folder_mode_rejects_missing_suffix(self) -> None:
        with self.assertRaises(ValueError):
            parse_folder_mode(Path("Field1"))

    def test_source_root_comes_from_format(self) -> None:
        self.assertEqual(source_root_for_format(FORMATS_BY_OPTION[1]).name, "videos")
        self.assertEqual(source_root_for_format(FORMATS_BY_OPTION[2]).name, "videos")

    def test_natural_filename_sorting(self) -> None:
        paths = [Path("clip10.mov"), Path("clip2.mov"), Path("clip1.mov")]
        sorted_names = [path.name for path in sorted(paths, key=natural_sort_key)]
        self.assertEqual(sorted_names, ["clip1.mov", "clip2.mov", "clip10.mov"])

    def test_text_position_defaults_to_center_and_can_move_vertically(self) -> None:
        self.assertEqual(text_y_expression(FORMATS_BY_OPTION[1]), "h*0.5000-text_h/2")
        self.assertEqual(text_y_expression(FORMATS_BY_OPTION[1], 25), "h*0.2500-text_h/2")

    def test_format_one_and_two_scale_clips_to_fit_canvas(self) -> None:
        for video_format in [FORMATS_BY_OPTION[1], FORMATS_BY_OPTION[2]]:
            video_filter = make_video_filter(video_format)
            self.assertIn(
                f"scale={video_format.width}:{video_format.height}:"
                "force_original_aspect_ratio=decrease",
                video_filter,
            )
            self.assertIn(
                f"pad={video_format.width}:{video_format.height}",
                video_filter,
            )
            self.assertNotIn("crop=", video_filter)

    def test_square_clip_scale_crops_to_square_then_fits_canvas(self) -> None:
        video_format = FORMATS_BY_OPTION[1]
        video_filter = make_video_filter(
            video_format,
            ClipScaleMode.SQUARE.value,
            75,
        )

        self.assertIn(
            r"crop=min(iw\,ih):min(iw\,ih):(iw-min(iw\,ih))*0.7500",
            video_filter,
        )
        self.assertIn(
            f"scale={video_format.width}:{video_format.height}:"
            "force_original_aspect_ratio=decrease",
            video_filter,
        )
        self.assertIn(
            f"pad={video_format.width}:{video_format.height}",
            video_filter,
        )

    def test_fill_clip_scale_crops_with_bounded_x_position(self) -> None:
        video_format = FORMATS_BY_OPTION[2]
        video_filter = make_video_filter(
            video_format,
            ClipScaleMode.FILL.value,
            125,
        )

        self.assertIn(
            f"scale={video_format.width}:{video_format.height}:"
            "force_original_aspect_ratio=increase",
            video_filter,
        )
        self.assertIn(
            f"crop={video_format.width}:{video_format.height}:"
            f"(iw-{video_format.width})*1.0000",
            video_filter,
        )

    def test_make_segment_uses_source_clip_scale_attributes(self) -> None:
        video_format = FORMATS_BY_OPTION[1]
        segment = ClipSegment(
            source=SourceVideo(
                Path("clip.mov"),
                4.0,
                scale_mode=ClipScaleMode.FILL.value,
                crop_x_percent=25,
            ),
            duration=1.25,
        )

        with patch("generate_video.ffmpeg_exe", return_value="ffmpeg"):
            with patch("generate_video.run_ffmpeg") as mock_run:
                make_segment(segment, video_format, Path("out.mp4"))

        command = mock_run.call_args.args[0]
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn(f"crop={video_format.width}:{video_format.height}", video_filter)
        self.assertIn(f"(iw-{video_format.width})*0.2500", video_filter)

    def test_create_cover_image_always_uses_vertical_fill_and_x_position(self) -> None:
        source = SourceVideo(
            Path("clip.mov"),
            4.0,
            scale_mode=ClipScaleMode.FIT.value,
            crop_x_percent=75,
        )

        with patch("generate_video.ffmpeg_exe", return_value="ffmpeg"):
            with patch("generate_video.run_ffmpeg") as mock_run:
                result = create_cover_image(
                    source,
                    1.25,
                    Path("cover.jpg"),
                )

        command = mock_run.call_args.args[0]
        self.assertEqual(result, Path("cover.jpg"))
        self.assertEqual(command[command.index("-ss") + 1], "1.250")
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertEqual(command[command.index("-update") + 1], "1")
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn("scale=1080:1920", video_filter)
        self.assertIn("crop=1080:1920", video_filter)
        self.assertIn(f"(iw-{COVER_IMAGE_FORMAT.width})*0.7500", video_filter)
        self.assertIn("format=yuvj420p", video_filter)
        self.assertNotIn("fps=", video_filter)
        self.assertNotIn("range=tv", video_filter)

    def test_cover_filter_is_vertical_fill_without_video_color_stages(self) -> None:
        video_filter = make_cover_image_filter(25)

        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=1080:1920:(iw-1080)*0.2500", video_filter)
        self.assertIn("format=yuvj420p", video_filter)
        self.assertNotIn("sidedata=", video_filter)

    def test_text_size_choice_defaults_and_scales(self) -> None:
        self.assertEqual(text_size_for_choice(""), TEXT_FONT_SIZE)
        self.assertEqual(text_size_for_choice("1"), 48)
        self.assertEqual(text_size_for_choice("3"), TEXT_FONT_SIZE)
        self.assertEqual(text_size_for_choice("5"), 84)

    def test_text_size_choice_rejects_unknown_option(self) -> None:
        with self.assertRaises(ValueError):
            text_size_for_choice("9")

    def test_prepare_text_uses_horizontal_padding_for_wrap_width(self) -> None:
        video_format = FORMATS_BY_OPTION[1]
        with patch("generate_video.wrap_words", return_value=["hello"]) as mock_wrap:
            prepare_text("hello", video_format, TEXT_FONT_SIZE, text_horizontal_padding=300)

        self.assertEqual(mock_wrap.call_args.args[3], video_format.width - 600)

    def test_create_cta_overlay_uses_reference_layout_for_vertical_format(self) -> None:
        overlay = create_cta_overlay_image(FORMATS_BY_OPTION[1])

        self.assertEqual(overlay.size, (1080, 1920))
        self.assertEqual(overlay.getpixel((0, 0))[3], 0)
        self.assertGreater(
            overlay.getpixel((CTA_APPSTORE_PADDING + 10, CTA_APPSTORE_PADDING + 10))[3],
            0,
        )

    def test_create_cta_overlay_uses_custom_text(self) -> None:
        with patch(
            "generate_video.draw_cta_text_box",
            side_effect=lambda image, *_args, **_kwargs: image,
        ) as mock_draw:
            create_cta_overlay_image(FORMATS_BY_OPTION[1], "Plan your next date night!")

        self.assertEqual(mock_draw.call_args.args[1], "Plan your next date night!")

    def test_cta_overlay_replaces_text_at_end_of_filter_graph(self) -> None:
        with patch("generate_video.ffmpeg_exe", return_value="ffmpeg"):
            with patch("generate_video.run_ffmpeg") as mock_run:
                overlay_text_and_music(
                    input_path=Path("input.mp4"),
                    output_path=Path("output.mp4"),
                    text_path=Path("overlay_text.txt"),
                    cta_overlay_path=Path("cta_overlay.png"),
                    font_size=TEXT_FONT_SIZE,
                    line_spacing=0,
                    output_duration=10.0,
                    music_path=Path("music.mp3"),
                    video_format=FORMATS_BY_OPTION[1],
                )

        command = mock_run.call_args.args[0]
        filter_complex = command[command.index("-filter_complex") + 1]
        self.assertIn("enable='lt(t\\,7.000)'", filter_complex)
        self.assertIn(
            "[with_text][2:v]overlay=0:0:format=auto:enable='gte(t\\,7.000)'",
            filter_complex,
        )
        self.assertIn("-loop", command)
        self.assertIn("cta_overlay.png", command)

    def test_overlay_can_render_without_music(self) -> None:
        with patch("generate_video.ffmpeg_exe", return_value="ffmpeg"):
            with patch("generate_video.run_ffmpeg") as mock_run:
                overlay_text_and_music(
                    input_path=Path("input.mp4"),
                    output_path=Path("output.mp4"),
                    text_path=None,
                    cta_overlay_path=Path("cta_overlay.png"),
                    font_size=TEXT_FONT_SIZE,
                    line_spacing=0,
                    output_duration=5.0,
                    music_path=None,
                    video_format=FORMATS_BY_OPTION[1],
                )

        command = mock_run.call_args.args[0]
        filter_complex = command[command.index("-filter_complex") + 1]
        self.assertIn("[base][1:v]overlay=0:0", filter_complex)
        self.assertEqual(command.count("-i"), 2)
        self.assertIn("-an", command)
        self.assertNotIn("1:a:0", command)

    def test_format_duration_uses_seconds_with_two_decimals(self) -> None:
        self.assertEqual(format_duration(7.255), "7.25s")
        self.assertEqual(format_duration(14.0), "14.00s")

    def test_music_track_listing_includes_duration(self) -> None:
        tracks = [Path("first.mp3"), Path("second.mp3")]
        with patch("generate_video.probe_duration", side_effect=[7.25, 14.0]):
            with patch("builtins.input", return_value="2"):
                with patch("builtins.print") as mock_print:
                    selected = ask_for_music_track(tracks)

        self.assertEqual(selected, tracks[1])
        printed_lines = [call.args[0] for call in mock_print.call_args_list]
        self.assertIn("1. first.mp3 (7.25s)", printed_lines)
        self.assertIn("2. second.mp3 (14.00s)", printed_lines)

    def test_random_clip_count_options_show_count_and_average_duration(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 4.0),
            SourceVideo(Path("clip2.mov"), 4.0),
            SourceVideo(Path("clip3.mov"), 4.0),
        ]
        options = random_clip_count_options(videos, 3.0)
        self.assertEqual([option.clip_count for option in options], [1, 2, 3])
        self.assertEqual(
            [round(option.average_duration, 2) for option in options],
            [3.0, 1.5, 1.0],
        )

    def test_random_plan_honors_selected_clip_count(self) -> None:
        random.seed(10)
        videos = [
            SourceVideo(Path("clip1.mov"), 4.0),
            SourceVideo(Path("clip2.mov"), 4.0),
            SourceVideo(Path("clip3.mov"), 4.0),
        ]
        segments = plan_random_segments(videos, 3.0, clip_count=3)
        self.assertEqual(len(segments), 3)
        self.assertAlmostEqual(sum(segment.duration for segment in segments), 3.0)

    def test_random_plan_rejects_unavailable_clip_count(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 4.0),
            SourceVideo(Path("clip2.mov"), 4.0),
        ]
        with self.assertRaises(ValueError):
            plan_random_segments(videos, 3.0, clip_count=3)

    def test_random_clip_count_prompt_accepts_option(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 4.0),
            SourceVideo(Path("clip2.mov"), 4.0),
            SourceVideo(Path("clip3.mov"), 4.0),
        ]
        with patch("builtins.input", return_value="2"):
            with patch("builtins.print") as mock_print:
                clip_count = ask_for_random_clip_count(videos, 3.0)

        self.assertEqual(clip_count, 2)
        printed_lines = [call.args[0] for call in mock_print.call_args_list]
        self.assertIn("2. 2 clips (avg 1.50s per clip)", printed_lines)

    def test_ordered_plan_uses_every_clip_in_natural_order(self) -> None:
        random.seed(10)
        videos = [
            SourceVideo(Path("clip10.mov"), 5.0),
            SourceVideo(Path("clip2.mov"), 3.0),
            SourceVideo(Path("clip1.mov"), 2.0),
        ]
        segments = plan_ordered_segments(videos, 6.0)
        self.assertEqual(
            [segment.source.path.name for segment in segments],
            ["clip1.mov", "clip2.mov", "clip10.mov"],
        )
        self.assertEqual(len(segments), 3)
        self.assertAlmostEqual(sum(segment.duration for segment in segments), 6.0)
        self.assertTrue(all(segment.duration > 0 for segment in segments))
        self.assertLessEqual(segments[0].duration, 2.0)
        self.assertLessEqual(segments[1].duration, 3.0)
        self.assertLessEqual(segments[2].duration, 5.0)

    def test_ordered_plan_uses_all_full_clips_when_music_is_longer_than_footage(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 2.0),
            SourceVideo(Path("clip2.mov"), 3.0),
        ]
        segments = plan_ordered_segments(videos, 10.0)
        self.assertEqual([segment.source.path.name for segment in segments], ["clip1.mov", "clip2.mov"])
        self.assertEqual([segment.duration for segment in segments], [2.0, 3.0])
        self.assertEqual(sum(segment.duration for segment in segments), 5.0)

    def test_ordered_plan_rejects_music_too_short_for_all_clips(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 2.0),
            SourceVideo(Path("clip2.mov"), 3.0),
            SourceVideo(Path("clip3.mov"), 4.0),
        ]
        with self.assertRaises(RuntimeError):
            plan_ordered_segments(videos, 0.02)

    def test_selected_plan_preserves_selection_order_and_explicit_ranges(self) -> None:
        videos = [
            SourceVideo(Path("clip10.mov"), 5.0, trim_start_seconds=1.0, trim_end_seconds=2.5),
            SourceVideo(Path("clip2.mov"), 3.0, trim_start_seconds=0.25, trim_end_seconds=2.0),
            SourceVideo(Path("clip1.mov"), 2.0, trim_start_seconds=0.5, trim_end_seconds=1.25),
        ]
        segments = plan_selected_segments(videos, 6.0)
        self.assertEqual(
            [segment.source.path.name for segment in segments],
            ["clip10.mov", "clip2.mov", "clip1.mov"],
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual([segment.start_time for segment in segments], [1.0, 0.25, 0.5])
        self.assertEqual([segment.duration for segment in segments], [1.5, 1.75, 0.75])

    def test_selected_plan_uses_full_ranges_regardless_of_music_length(self) -> None:
        videos = [
            SourceVideo(Path("clip1.mov"), 2.0),
            SourceVideo(Path("clip2.mov"), 3.0),
        ]
        short_music_segments = plan_selected_segments(videos, 1.0)
        long_music_segments = plan_selected_segments(videos, 10.0)
        self.assertEqual([segment.duration for segment in short_music_segments], [2.0, 3.0])
        self.assertEqual([segment.duration for segment in long_music_segments], [2.0, 3.0])

    def test_final_output_stops_at_shorter_of_timeline_and_song(self) -> None:
        segments = [
            ClipSegment(SourceVideo(Path("first.mov"), 2.0), duration=2.0),
            ClipSegment(SourceVideo(Path("second.mov"), 3.0), duration=3.0),
        ]

        self.assertEqual(final_output_duration(segments, 10.0), 5.0)
        self.assertEqual(final_output_duration(segments, 4.0), 4.0)

    def test_final_output_uses_full_timeline_without_music(self) -> None:
        segments = [
            ClipSegment(SourceVideo(Path("first.mov"), 2.0), duration=2.0),
            ClipSegment(SourceVideo(Path("second.mov"), 3.0), duration=3.0),
        ]

        self.assertEqual(final_output_duration(segments, None), 5.0)

    def test_make_segment_uses_explicit_timeline_trim_start(self) -> None:
        segment = ClipSegment(
            source=SourceVideo(Path("clip.mov"), 5.0),
            duration=1.5,
            start_time=2.25,
        )

        with patch("generate_video.ffmpeg_exe", return_value="ffmpeg"):
            with patch("generate_video.run_ffmpeg") as mock_run:
                make_segment(segment, FORMATS_BY_OPTION[1], Path("out.mp4"))

        command = mock_run.call_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "2.250")
        self.assertEqual(command[command.index("-t") + 1], "1.500")

    def test_random_plan_matches_music_when_enough_footage_exists(self) -> None:
        random.seed(10)
        videos = [
            SourceVideo(Path("clip1.mov"), 4.0),
            SourceVideo(Path("clip2.mov"), 4.0),
            SourceVideo(Path("clip3.mov"), 4.0),
        ]
        segments = plan_random_segments(videos, 2.0)
        self.assertAlmostEqual(sum(segment.duration for segment in segments), 2.0)
        self.assertEqual(len({segment.source.path for segment in segments}), len(segments))

    def test_random_plan_shortens_when_music_is_longer_than_footage(self) -> None:
        random.seed(10)
        videos = [
            SourceVideo(Path("clip1.mov"), 1.0),
            SourceVideo(Path("clip2.mov"), 1.0),
            SourceVideo(Path("clip3.mov"), 1.0),
        ]
        segments = plan_random_segments(videos, 10.0)
        self.assertAlmostEqual(sum(segment.duration for segment in segments), 1.5)

    def test_single_plan_uses_shorter_music_or_video_duration(self) -> None:
        video = SourceVideo(Path("clip.mov"), 8.0)
        self.assertEqual(plan_single_segment(video, 3.0)[0].duration, 3.0)
        self.assertEqual(plan_single_segment(video, 12.0)[0].duration, 8.0)


if __name__ == "__main__":
    unittest.main()
