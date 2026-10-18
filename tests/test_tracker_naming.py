import pytest

from src.meta import Meta
from src.trackers.naming import select_aka


@pytest.mark.parametrize('original,expected_aka', [
    ('काल्पनिक शीर्षक', ''),
    ('架空の題名THEサンプル', ''),
    ('Exêmple fictif: Histôire imaginaire', 'AKA Exêmple fictif: Histôire imaginaire'),
    ('Titre inventé naïf', 'AKA Titre inventé naïf'),
    ('Exemple (Histoire héroïque)', 'AKA Exemple (Histoire héroïque)'),
])
def test_aka_requires_latin_script(original, expected_aka):
    assert select_aka(Meta(original_title=original), 'Example Title') == expected_aka


@pytest.mark.parametrize('primary,alternate,expected', [
    ('Example Story', 'Example Story', ''),
    ('Ornek 2 Ask', 'Örnek 2: Aşk', ''),
    ('Example Hero: Let A Story Begin', 'Example Hero', ''),
    ('Examples and Stories', 'Examples & Stories', ''),
    ('The Example Character', 'example CHARACTER', ''),
    ('Revisited: Example By Example', 'Example by Example', ''),
    ('Example Bird: Beginning', 'example bird - beginning of story', 'AKA example bird - beginning of story'),
    ('Example Bird: Beginning', 'EXAMPLE BIRD / BEGINNING: More', 'AKA EXAMPLE BIRD / BEGINNING: More'),
    ('An Example & A Story', 'Example and Story', ''),
    ('Example Singer & Example Drummer - Light & The Sound', 'Light & the Sound: Example Singer & Example Drummer',
     ''),
    ('The Example Story', 'Story, Example!', ''),
    ('Un été inventé', 'Ete invente', ''),
    ('Example Storybook', 'Story', 'AKA Story'),
    ('Example Story 2', 'Example Story 3', 'AKA Example Story 3'),
    ('Example Bird', 'A Story About Example Bird', 'AKA A Story About Example Bird'),
    ('Example Bird', 'The Example Bird: More', 'AKA The Example Bird: More'),
])
def test_overlapping_aka_titles(primary, alternate, expected):
    meta = Meta(original_title=alternate)
    before = meta.to_dict()
    assert select_aka(meta, primary) == expected
    assert meta.to_dict() == before


def test_aka_fallback_and_romanized_alias():
    meta = Meta(original_title='한국어', imdb_info={'aka': 'English'})
    assert select_aka(meta, 'English') == ''
    meta.imdb_info['akas'] = [{'title': 'Hangug-eo', 'attributes': [{'text': 'romanized title'}]}]
    assert select_aka(meta, 'English') == 'AKA Hangug-eo'
    meta.no_aka = True
    assert select_aka(meta, 'English') == ''


@pytest.mark.parametrize('imdb_aka,original,prepared,anime,expected', [
    ('IMDb alternate', 'TMDB original', 'AKA Anime title', True, 'AKA IMDb alternate'),
    ('PRIMARY!', 'TMDB original', '', False, 'AKA TMDB original'),
    ('한국어', 'TMDB original', '', False, ''),
    ('한국어', 'TMDB original', 'AKA Anime title', True, 'AKA Anime title'),
    ('Primary', 'PRIMARY!', 'AKA Anime title', True, 'AKA Anime title'),
    ('Primary', 'PRIMARY!', 'AKA Anime title', False, ''),
])
def test_aka_source_priority(imdb_aka, original, prepared, anime, expected):
    meta = Meta(original_title=original, imdb_info={'aka': imdb_aka}, retrieved_aka=prepared, anime=anime)
    before = meta.to_dict()
    assert select_aka(meta, 'Primary') == expected
    assert meta.to_dict() == before


@pytest.mark.parametrize('language,expected', [('Korean', 'AKA Hangug-eo'), (None, 'AKA Hangug-eo'), ('Unknown language', '')])
def test_romanization_filters_aliases_before_selection(language, expected):
    meta = Meta(original_title='한국어', original_language='ko', imdb_info={'aka': 'Primary', 'akas': [
        None,
        {'title': 'Wrong language', 'language': 'Japanese', 'attributes': ['romanized title']},
        {'title': 'Unmarked alias', 'language': 'Korean'},
        {'title': '한국어', 'language': 'Korean', 'attributes': ['romanized title']},
        {'title': 'PRIMARY!', 'language': 'Korean', 'attributes': ['romanised title']},
        {'title': 'Hangug-eo', 'language': language, 'attributes': ['transliterated title']},
    ]})
    assert select_aka(meta, 'Primary') == expected
