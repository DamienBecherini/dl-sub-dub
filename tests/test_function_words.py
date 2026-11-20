"""The lists of function words: one file a language (those of the 25 languages of Parakeet come with the tool), the
user's own in the settings folder (a language added, a list replaced), read again when they change, moved with the
settings; and the command that measures a text against them."""
import json

import pytest

from subtitles import config, data_location, language

PARAKEET = "bg hr cs da nl en et fi fr de el hu it lv lt mt pl pt ro sk sl es sv ru uk".split()

# A few sentences of everyday speech in languages added on 20/11/2026: each is found, by its own list.
SPOKEN = {
    "pl": "Nie wiem, co to jest, ale myślę, że to się uda. Czy ty już tam byłeś? Tak, byłem tam z nim i to nie jest tak, jak mówisz.",
    "sv": "Jag vet inte vad det är, men jag tror att det kommer att gå bra. Har du varit där? Ja, jag var där med honom och det är inte som du säger.",
    "da": "Jeg ved ikke, hvad det er, men jeg tror, at det nok skal gå. Har du været der? Ja, jeg var der med ham, og det er ikke som du siger.",
    "ru": "Я не знаю, что это такое, но я думаю, что всё получится. Ты уже был там? Да, я был там с ним, и это не так, как ты говоришь.",
    "uk": "Я не знаю, що це таке, але я думаю, що все вийде. Ти вже був там? Так, я був там з ним, і це не так, як ти кажеш.",
    "cs": "Nevím, co to je, ale myslím, že se to povede. Už jsi tam byl? Ano, byl jsem tam s ním a není to tak, jak říkáš.",
    "sk": "Neviem, čo to je, ale myslím, že sa to podarí. Už si tam bol? Áno, bol som tam s ním a nie je to tak, ako hovoríš.",
    "el": "Δεν ξέρω τι είναι αυτό, αλλά νομίζω ότι θα τα καταφέρουμε. Έχεις πάει εκεί; Ναι, ήμουν εκεί μαζί του και δεν είναι όπως το λες.",
    "fi": "En tiedä, mitä se on, mutta luulen, että se onnistuu. Oletko sinä jo ollut siellä? Kyllä, minä olin siellä ja se ei ole niin kuin sanot.",
    "hu": "Nem tudom, hogy mi ez, de azt hiszem, hogy sikerülni fog. Voltál már ott? Igen, ott voltam vele, és ez nem úgy van, ahogy mondod.",
    "ro": "Nu știu ce este asta, dar cred că o să reușim. Ai fost deja acolo? Da, am fost acolo cu el și nu este așa cum spui tu.",
    "mt": "Ma nafx x'inhu dan, imma naħseb li se jirnexxi. Kont diġà hemm? Iva, kont hemm miegħu u mhux kif qed tgħid int, għax dan kien ta' ħija.",
}


@pytest.fixture
def own(tmp_path, monkeypatch):
    """The folder of the user's lists, empty."""
    folder = tmp_path / "lists"
    folder.mkdir()
    monkeypatch.setattr(language, "USER_LISTS", folder)
    return folder


def test_the_tool_comes_with_the_lists_of_the_languages_of_parakeet():
    sets = language.word_sets()
    assert sorted(sets) == sorted(PARAKEET) and all(len(words) >= 20 for words in sets.values())
    assert "le" in sets["fr"] and "się" in sets["pl"] and "что" in sets["ru"] and "και" in sets["el"]


@pytest.mark.parametrize("code", sorted(SPOKEN))
def test_a_language_is_found_by_its_own_list(code):
    count, scores = language.shares(SPOKEN[code])
    assert language.detect(SPOKEN[code]) == code and scores[code] >= language.DOUBT_USUAL
    assert language.doubt(" ".join([SPOKEN[code]] * 10), code) is None


def test_the_user_adds_a_language_and_replaces_a_list(own):
    turkish = "Ben bunu bilmiyorum ama bu çok iyi bir şey ve o da bunu biliyor. Sen ne için buraya geldin, bu ne ve o ne?"
    assert language.detect(turkish) != "tr" and language.doubt(" ".join([turkish] * 10), "tr") is None     # no list: not found, not judged
    (own / "tr.txt").write_text("# Türkçe\nbir bu ve ne o da ben sen için çok ama\nde ile\n", encoding="utf-8")
    assert language.detect(turkish) == "tr" and language.word_sets()["tr"] >= {"bir", "ile", "çok"}
    assert language.doubt(" ".join([turkish] * 10), "tr") is None                   # judged now, and right
    assert language.doubt(" ".join(["laqbas nehmid rabbina wahed meja"] * 60), "tr")["code"] == "tr"
    french = "le la les un une des et est que qui dans pour pas ce il elle nous vous"
    assert language.detect(french) == "fr"
    (own / "fr.txt").write_text("zut flûte\n", encoding="utf-8")                    # the user's list takes the place of the tool's
    assert language.word_sets()["fr"] == {"zut", "flûte"} and language.detect(french) != "fr"
    (own / "fr.txt").unlink()                                                       # taken away: the list of the tool is back
    assert language.detect(french) == "fr"


def test_a_file_that_is_not_a_list_is_left_out(own):
    (own / "notes about lists.txt").write_text("le la les", encoding="utf-8")       # not a language code
    (own / "de.txt").write_text("# nothing but a comment\n", encoding="utf-8")      # no word: the list of the tool stays
    (own / "it.txt").write_bytes(b"\xff\xfe\x00bad")                                # not UTF-8
    sets = language.word_sets()
    assert sorted(sets) == sorted(PARAKEET) and "der" in sets["de"] and "che" in sets["it"]


def test_the_lists_of_the_user_move_with_the_settings(tmp_path, monkeypatch):
    fixed = tmp_path / "fixed" / "subtitles"
    (fixed / "function_words").mkdir(parents=True)
    (fixed / "config.json").write_text(json.dumps({"output_dir": "X"}), encoding="utf-8")
    (fixed / "function_words" / "tr.txt").write_text("bir bu ve", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "fixed"))
    monkeypatch.setattr(config, "USER_FILE", fixed / "config.json")
    try:
        target = tmp_path / "data"
        data_location.move(str(target))
        assert (target / "function_words" / "tr.txt").read_text(encoding="utf-8") == "bir bu ve"
        assert not (fixed / "function_words").exists() and language.USER_LISTS == target / "function_words"
        assert "tr" in language.word_sets()
    finally:
        monkeypatch.undo()
        data_location.repoint()


def test_the_command_measures_a_text_against_the_lists(tmp_path, capsys):
    srt = tmp_path / "v.fr.srt"
    lines = ["Bonjour et bienvenue dans ce court épisode pour vous.", "Il faut garder trois copies de vos données sur deux supports."]
    srt.write_text("\n\n".join(f"{n + 1}\n00:00:0{n % 9},000 --> 00:00:0{n % 9 + 1},000\n{lines[n % 2]}" for n in range(40)), encoding="utf-8")
    assert language.main([str(srt)]) == 0
    out = capsys.readouterr().out
    assert "language found: fr" in out and "right:" in out and "lists: 25" in out
    assert language.main([str(srt), "en"]) == 0 and "doubtful: under 12% for en" in capsys.readouterr().out
    tr = tmp_path / "transcription.json"
    tr.write_text(json.dumps({"text": "laqbas nehmid rabbina wahed meja " * 60}), encoding="utf-8")
    assert language.main([str(tr)]) == 0
    out = capsys.readouterr().out
    assert "language found: none" in out and "doubtful: under 12% for any language" in out
    assert language.main([]) == 2
