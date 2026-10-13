"""Matcher rules in clean.py. Runs under pytest, or standalone:
    .venv/bin/python tests/test_clean.py
"""

from music_scout.clean import artist_matches, bracketed_titles, mentioned, pick_version


def _hits(*names):
    return [{"name": n} for n in names]


def _pick(wanted, names, **post):
    hit = pick_version(wanted, _hits(*names), **post)
    return hit and hit["name"]


# ---- the post names no version: the plain cut wins ----

def test_plain_post_skips_remix():
    assert _pick("In The Mood” (feat. DEVAURA)",
                 ["In The Mood - Meg Ward Remix", "In The Mood"]) == "In The Mood"


def test_plain_post_skips_instrumental_and_live():
    assert _pick("WHO", ["WHO [INSTRUMENTAL]", "WHO"]) == "WHO"
    assert _pick("FMU", ["FMU - Live", "FMU"]) == "FMU"


def test_plain_post_skips_named_remixes():
    assert _pick("Ponderosa Sun Club",
                 ["Ponderosa Sun Club (The Sleeping Cliffs Remix)",
                  "Ponderosa Sun Club (DeepSonics Remix)",
                  "Ponderosa Sun Club"]) == "Ponderosa Sun Club"


def test_benign_tags_count_as_plain():
    assert _pick("Song", ["Song - X Remix", "Song - Radio Edit"]) == "Song - Radio Edit"
    assert _pick("Live Forever", ["Live Forever - Live at Knebworth",
                                  "Live Forever - Remastered"]) == "Live Forever - Remastered"
    assert _pick("Daylight (feat. Sharin) (Original Mix)",
                 ["Daylight - X Remix", "Daylight"]) == "Daylight"


def test_only_mixes_exist_takes_top_hit():
    # Peter Gabriel ships "Bright-Side" / "Dark-Side" mixes and no plain cut.
    assert _pick("One By One", ["One by One - Bright-Side Mix",
                                "One by One - Dark-Side Mix"]) == "One by One - Bright-Side Mix"


# ---- the post names a version: that version wins ----

def test_named_remix_in_title_wins():
    assert _pick("Ponderosa Sun Club (The Sleeping Cliffs Remix)",
                 ["Ponderosa Sun Club",
                  "Ponderosa Sun Club (DeepSonics Remix)",
                  "Ponderosa Sun Club (The Sleeping Cliffs Remix)"]
                 ) == "Ponderosa Sun Club (The Sleeping Cliffs Remix)"


def test_named_remix_matches_spotify_dash_form():
    assert _pick("Duel pt.2 (Art Of Tones Remix)",
                 ["Duel", "Duel, pt. 2 - Art Of Tones remix extended"]
                 ) == "Duel, pt. 2 - Art Of Tones remix extended"


def test_remixer_credited_as_artist():
    hits = [{"name": "Ride It Out", "artists": [{"name": "LEISURE"}]},
            {"name": "Ride It Out - Remix", "artists": [{"name": "Poolside"}, {"name": "LEISURE"}]}]
    assert pick_version("Ride It Out (Poolside Remix)", hits)["name"] == "Ride It Out - Remix"


def test_truncated_remix_tag_still_counts():
    assert _pick("All I See (Terrence Parker Extended Remi...",
                 ["All I See", "All I See - Terrence Parker Extended Remix"]
                 ) == "All I See - Terrence Parker Extended Remix"


def test_named_live_and_acoustic_win():
    assert _pick("FMU (Live)", ["FMU", "FMU - Live"]) == "FMU - Live"
    assert _pick("Heavy Metal (UN-unplugged)",
                 ["Heavy Metal", "Heavy Metal - UN-unplugged"]) == "Heavy Metal - UN-unplugged"


def test_named_version_missing_means_no_match():
    # The SHEE edit isn't on Spotify; another remix or the original is a wrong add.
    assert _pick("Feeling For You(SHEE Edit)",
                 ["Feeling For You - Fcukers & Junior Sanchez Remix", "Feeling For You"]) is None
    assert _pick("Song (Live)", ["Song"]) is None


def test_post_title_names_version_when_search_title_is_a_correction():
    # Review corrected the search to the bare title; the scraped headline still names the edit.
    assert _pick("Feeling For You", ["Feeling For You - Fcukers & Junior Sanchez Remix",
                                     "Feeling For You"],
                 post_title="Feeling For You(SHEE Edit)") is None
    assert _pick("Ride It Out", ["Ride It Out", "Ride It Out - Poolside Remix"],
                 post_title="Ride It Out (Poolside Remix)") == "Ride It Out - Poolside Remix"


def test_version_in_post_text_preferred_when_on_spotify():
    text = "Madi Diaz shares a stripped acoustic version of Heavy Metal"
    assert _pick("Heavy Metal", ["Heavy Metal", "Heavy Metal - Acoustic"],
                 post_text=text) == "Heavy Metal - Acoustic"
    # Mentioned but not on Spotify: fall back to the plain cut, don't drop the song.
    assert _pick("Heavy Metal", ["Heavy Metal"], post_text=text) == "Heavy Metal"


def test_incidental_live_in_post_text_is_not_a_version():
    assert _pick("Song", ["Song - Live", "Song"],
                 post_text="Catch them live this fall on tour") == "Song"


# ---- artist / headline helpers ----

def test_artist_check():
    assert artist_matches("@", ["@"])
    assert not artist_matches("Re.You", ["Carly Rae Jepsen"])
    assert not artist_matches("Count Shaw", ["wishburn"])


def test_bracketed_titles_and_mentions():
    head = "Royal TruxのJennifer Herrema率いる「Black Bananas」が 先行シングル「Turkey Burgers」を世界解禁"
    assert bracketed_titles(head) == ["Turkey Burgers", "Black Bananas"]
    assert bracketed_titles("浮遊感と「遠回り」の美学：MOLØが先行曲「Malmö」") == ["Malmö"]
    assert mentioned("Slow Pulp", "──Slow Pulpが今秋放つ")
    assert not mentioned("Ane", "Shane Brunが")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
    print(f"{len(tests)} passed")
