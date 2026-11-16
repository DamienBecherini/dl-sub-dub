"""The voice reference of a sentence: never another person's line (a man dubbed with the voice of the woman who
answered him), and its own audio alone when no nearby line has the same voice."""
from subtitles import dubbing_worker as w

# 6:50 of the episode: a man's short question, the woman's answer, then a longer line of the man.
CUES = [(46.7, 50.7, "un appartement qui communique avec la galerie d'art"),      # 0 chief
        (50.8, 53.2, "Pourquoi les nanas préfèrent toujours les bad guys ?"),     # 1 the man (2.4 s)
        (53.3, 55.9, "Eh pas de généralité, venez, s'il te plaît."),              # 2 the woman
        (57.4, 60.9, "Elle est à un antiquaire parisien."),                        # 3 chief
        (77.0, 78.0, "Je peux même faire mieux."),                                # 4 the man
        (81.1, 83.3, "Mais pourquoi il n'appellerait pas ?")]                     # 5 the man
VOICE = {0: "chief", 1: "man", 2: "woman", 3: "chief", 4: "man", 5: "man"}
same = lambda i, j: VOICE[i] == VOICE[j]  # noqa: E731


def test_a_short_sentence_takes_nearby_lines_of_its_own_voice_only():
    lines = w.reference_lines(CUES, 50.8, 53.2, same)
    assert lines[0] == 1 and 2 not in lines and 0 not in lines and 3 not in lines
    assert set(lines) <= {1, 4, 5} and sum(CUES[i][1] - CUES[i][0] for i in lines) >= w.REF_MIN_S


def test_no_line_of_the_same_voice_nearby_keeps_its_own_audio():
    lines = w.reference_lines(CUES, 53.3, 55.9, same)        # the woman: nobody else speaks like her here
    assert lines == [2]


def test_without_the_speaker_model_never_a_neighbour():
    assert w.reference_lines(CUES, 50.8, 53.2, None) == [1]


def test_a_sentence_long_enough_keeps_its_lines_and_a_reply_inside_it_is_left_out():
    cues = [(0.0, 3.5, "Une longue phrase du chef"), (3.6, 4.2, "Bien."), (4.3, 9.0, "et la suite de sa phrase")]
    voice = {0: "chief", 1: "woman", 2: "chief"}
    lines = w.reference_lines(cues, 0.0, 9.0, lambda i, j: voice[i] == voice[j])
    assert lines == [0, 2]


def test_never_over_ten_seconds():
    cues = [(i * 4.0, i * 4.0 + 3.9, f"phrase {i}") for i in range(6)]
    lines = w.reference_lines(cues, 0.0, 23.5, lambda i, j: True)
    assert sum(cues[i][1] - cues[i][0] for i in lines) <= w.REF_MAX_S


def test_the_voice_of_a_subtitle_for_its_speaker_vector_is_never_too_short():
    r = 16000
    assert w.speaker_bounds(60 * r, 10.0, 12.0) == (10 * r, 12 * r, 0)                    # long enough: as it is
    s, e, pad = w.speaker_bounds(60 * r, 10.0, 10.05)                                     # 0.05 s: widened around it
    assert e - s == int(w.SPEAKER_MIN_S * r) and s < 10 * r < e and pad == 0
    s, e, pad = w.speaker_bounds(60 * r, 59.98, 61.0)                                     # the very end, past the audio
    assert (s, e, pad) == (60 * r - int(w.SPEAKER_MIN_S * r), 60 * r, 0)
    s, e, pad = w.speaker_bounds(60 * r, 70.0, 71.0)                                      # wholly past it
    assert e == 60 * r and e - s == int(w.SPEAKER_MIN_S * r)
    assert w.speaker_bounds(r // 10, 0.0, 0.05) == (0, r // 10, int(w.SPEAKER_MIN_S * r) - r // 10)   # tiny audio: silence added
