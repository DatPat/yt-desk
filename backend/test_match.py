from __future__ import annotations

import unittest

from backend.match import compare_playlists, dedupe_ordered, group_songs, identity_for, unique_song_ids


def vid(i, title, channel="", duration=200, **extra):
    row = {"id": i, "title": title, "channel": channel, "duration": duration}
    row.update(extra)
    return row


class IdentityTests(unittest.TestCase):
    def test_official_vs_lyrics_vs_topic(self):
        a = identity_for(vid("1", "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 200))
        b = identity_for(vid("2", "The Weeknd - Blinding Lights (Official Audio)", "LyricsForEveryone", 201))
        c = identity_for(vid("3", "Blinding Lights", "The Weeknd - Topic", 200))
        self.assertEqual(a.artist_key, "weeknd")
        self.assertEqual(a.track_key, "blinding lights")
        self.assertEqual(a.artist_key, b.artist_key)
        self.assertEqual(a.track_key, b.track_key)
        self.assertEqual(c.artist_key, "weeknd")
        self.assertEqual(c.track_key, "blinding lights")

    def test_accent_and_the_prefix(self):
        a = identity_for(vid("1", "Beyoncé - Halo (Official Video)", "BeyonceVEVO", 230))
        b = identity_for(vid("2", "Beyonce - Halo (Lyric Video)", "PopLyrics", 231))
        self.assertEqual(a.artist_key, b.artist_key)
        self.assertEqual(a.track_key, "halo")

    def test_feat_stripped(self):
        a = identity_for(vid("1", "Calvin Harris feat. Rihanna - This Is What You Came For", "CHVEVO", 222))
        b = identity_for(vid("2", "Calvin Harris - This Is What You Came For (Official Video)", "CHVEVO", 222))
        self.assertEqual(a.track_key, b.track_key)
        self.assertEqual(a.artist_key, "calvin harris")

    def test_short_title_needs_artist(self):
        ident = identity_for(vid("1", "Love", "RandomUploads", 210))
        self.assertFalse(ident.groupable)

    def test_topic_short_title_is_groupable(self):
        ident = identity_for(vid("1", "Love", "Adele - Topic", 210))
        self.assertTrue(ident.groupable)
        self.assertEqual(ident.artist_key, "adele")

    def test_music_tags(self):
        ident = identity_for(
            vid("1", "whatever the uploader typed", "x", 180, artist="Daft Punk", track="Get Lucky")
        )
        self.assertEqual(ident.artist_key, "daft punk")
        self.assertEqual(ident.track_key, "get lucky")


class GroupTests(unittest.TestCase):
    def test_same_song_different_uploaders(self):
        videos = [
            vid("a", "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 200),
            vid("b", "The Weeknd - Blinding Lights (Lyrics)", "Nightwave Lyrics", 201),
            vid("c", "Blinding Lights", "The Weeknd - Topic", 200),
            vid("d", "Blinding Lights", "RandomLyricsTV", 202),
        ]
        groups = group_songs(videos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].distinct_ids, 4)
        self.assertIn("weeknd", groups[0].artist.lower())

    def test_does_not_merge_different_artists_named_hello(self):
        videos = [
            vid("a", "Hello", "Adele - Topic", 295),
            vid("b", "Hello", "Lionel Richie - Topic", 249),
        ]
        self.assertEqual(group_songs(videos), [])

    def test_does_not_merge_far_durations(self):
        videos = [
            vid("a", "Rick Astley - Never Gonna Give You Up", "RickAstleyVEVO", 213),
            vid("b", "Rick Astley - Never Gonna Give You Up (Live 12 min)", "Concerts", 720),
        ]
        self.assertEqual(group_songs(videos), [])

    def test_rickroll_without_artist_in_one_title(self):
        videos = [
            vid("a", "Rick Astley - Never Gonna Give You Up (Official Video)", "RickAstleyVEVO", 213),
            vid("b", "Never Gonna Give You Up", "MemeDump", 213),
        ]
        groups = group_songs(videos)
        self.assertEqual(len(groups), 1)

    def test_unique_ids_picks_one_per_song(self):
        videos = [
            vid("a", "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 200),
            vid("b", "The Weeknd - Blinding Lights (Lyrics)", "x", 201),
            vid("c", "Daft Punk - Get Lucky (Official Video)", "DaftPunkVEVO", 248),
        ]
        picked = unique_song_ids(videos)
        self.assertEqual(len(picked), 2)
        self.assertIn("c", picked)
        self.assertTrue(("a" in picked) ^ ("b" in picked) or True)
        self.assertEqual(len(set(picked) & {"a", "b"}), 1)

    def test_dedupe_ordered_keeps_first_song(self):
        videos = [
            vid("a", "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 200),
            vid("x", "Intro", "Random", 12),
            vid("b", "The Weeknd - Blinding Lights (Lyrics)", "Nightwave Lyrics", 201),
        ]
        kept, dropped = dedupe_ordered(videos, mode="song")
        ids = [v["id"] for v in kept]
        self.assertEqual(ids[0], "a")
        self.assertIn("x", ids)
        self.assertNotIn("b", ids)
        self.assertTrue(any(d.get("drop_reason") == "same song" for d in dropped))

    def test_compare_playlists_song_and_id(self):
        left = [
            vid("aaaaaaaaaaa", "The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", 200),
            vid("bbbbbbbbbbb", "Daft Punk - Get Lucky (Official Video)", "DaftPunkVEVO", 248),
        ]
        right = [
            vid("ccccccccccc", "The Weeknd - Blinding Lights (Lyrics)", "Nightwave Lyrics", 201),
            vid("ddddddddddd", "Rick Astley - Never Gonna Give You Up (Official Video)", "RickAstleyVEVO", 213),
        ]
        song = compare_playlists(left, right, mode="song")
        self.assertEqual(song["counts"]["both"], 1)
        self.assertEqual(song["counts"]["only_left"], 1)
        self.assertEqual(song["counts"]["only_right"], 1)
        self.assertEqual(song["only_left"][0]["id"], "bbbbbbbbbbb")
        self.assertEqual(song["only_right"][0]["id"], "ddddddddddd")
        ident = compare_playlists(left, right, mode="video")
        self.assertEqual(ident["counts"]["both"], 0)
        self.assertEqual(ident["counts"]["only_left"], 2)
        self.assertEqual(ident["counts"]["only_right"], 2)


if __name__ == "__main__":
    unittest.main()
