from __future__ import annotations

import unittest

from backend.media_tags import ID_PREFIX, library_stem, safe_filename


class FilenameTests(unittest.TestCase):
    def test_safe_filename_strips_windows_junk(self):
        self.assertNotIn(":", safe_filename("A:B/C|D?"))
        self.assertEqual(safe_filename("  ..  "), "audio")
        self.assertEqual(safe_filename("CON"), "_CON")

    def test_library_stem_uses_artist_and_song_not_video_id(self):
        video = {
            "id": "TJ4INx5lfe4",
            "title": "Apparat - Goodbye (Official Audio)",
            "channel": "Apparat",
            "artist": "",
            "track": "",
        }
        stem = library_stem(video, "TJ4INx5lfe4")
        self.assertEqual(stem, "Apparat - Goodbye")
        self.assertNotIn("TJ4INx5lfe4", stem)

    def test_id_prefix_detects_legacy_names(self):
        match = ID_PREFIX.match("TJ4INx5lfe4 - Apparat - Goodbye (Official Audio).opus")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "TJ4INx5lfe4")


class SkipExistingTests(unittest.TestCase):
    def test_parse_ls_keeps_spaces_in_names(self):
        from backend.android_usb import _parse_adb_ls, _should_skip_existing

        listing = """
total 12
-rw-rw---- 1 u0_a123 media_rw 4401673 2026-09-03 12:00 Apparat - Goodbye.opus
-rw-rw---- 1 u0_a123 media_rw 12 2026-09-03 12:00 tiny.opus
drwxrwx--- 2 u0_a123 media_rw 3452 2026-09-03 12:00 Playlists
Apparat - Goodbye.opus
"""
        parsed = _parse_adb_ls(listing)
        self.assertEqual(parsed.get("Apparat - Goodbye.opus"), 4401673)
        self.assertNotIn("Goodbye.opus", parsed)
        self.assertTrue(_should_skip_existing(True, 4401673, 4401673))
        self.assertTrue(_should_skip_existing(True, 4401673, -1))
        self.assertTrue(_should_skip_existing(True, 4401673, 0))
        self.assertFalse(_should_skip_existing(True, 4401673, 12))
        self.assertFalse(_should_skip_existing(False, 4401673, None))


class PlaylistM3uTests(unittest.TestCase):
    def test_m3u8_points_at_music_files(self):
        from backend.android_usb import m3u8_text

        text = m3u8_text(
            "downers",
            [
                {"filename": "Apparat - Goodbye.opus", "duration": 237, "display": "Apparat - Goodbye"},
                {"filename": "Song, with comma.opus", "duration": 12, "display": "Artist - Song, with comma"},
            ],
        )
        self.assertIn("#EXTM3U", text)
        self.assertIn("#PLAYLIST:downers", text)
        self.assertIn("#EXTINF:237,Apparat - Goodbye", text)
        self.assertIn("../Apparat - Goodbye.opus", text)
        self.assertIn("../Song, with comma.opus", text)
        self.assertTrue(text.endswith("\n"))


class PlaylistChangeTests(unittest.TestCase):
    def test_new_ids_are_those_missing_from_snapshot(self):
        old = {"aaa", "bbb"}
        videos = [{"id": "bbb", "title": "Keep"}, {"id": "ccc", "title": "New"}]
        added = [v for v in videos if v["id"] not in old]
        removed = [vid for vid in old if vid not in {v["id"] for v in videos}]
        self.assertEqual([v["id"] for v in added], ["ccc"])
        self.assertEqual(removed, ["aaa"])


if __name__ == "__main__":
    unittest.main()
