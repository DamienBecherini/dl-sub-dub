"""A transcription that looks little like its language (an engine that does not know the language spoken writes it by
ear): measured from its function words, said in the log, and the video of a project waits for the user before anything
is reviewed or translated; a model can be asked which language it hears."""
import json

from helpers import Run, update
from subtitles import config, language, pipeline, translation
from subtitles.states import TaskState
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

LOCAL = {"id": "local", "type": "openai", "name": "Local", "url": "http://127.0.0.1:8080", "model": "qwen"}
CLAUDE = {"id": "claude", "type": "claude", "name": "Claude", "model": "claude-sonnet-5-5"}
NONE = {"id": "none", "type": "none", "name": "None"}

# Arabic written by ear by an engine that does not know it, as it came out on 19/11/2026: three function words of
# Italian among 41 words (7 %: enough to be found "Italian", far from the 15 % of a real transcription).
BY_EAR = ("laqbas nehmid rabbina wal insan muhayar. fi wahed fil meja che messibtahena. ennemishmuhajar fi wasajful hajwawe. "
          "il muhinit tahin nehmed rabbin. nimishmuqajjar fiom fal insenn. tmenin fil meja minnu e hirwaqi. "
          "musayjar zemassil bin spelum aqhadeija. bessit tlahzu hagahina awul idrok.")
FRENCH = ("Bonjour et bienvenue dans ce court épisode. La règle est simple à retenir pour tout le monde. Il faut garder "
          "trois copies de vos données sur deux supports. Un disque peut tomber en panne et un appartement peut brûler. "
          "Avec trois copies bien réparties vous ne perdez pas vos photos. Et surtout il faut tester une restauration de "
          "temps en temps.")


def text(sample: str, words: int = 260) -> str:
    """The sample said again until it has `words` words."""
    once = sample.split()
    return " ".join((once * (words // len(once) + 1))[:words])


def test_a_transcription_written_by_ear_is_doubtful():
    assert language.detect(text(BY_EAR)) == "it"                           # found "Italian", just above the bar
    d = language.doubt(text(BY_EAR), "it")
    assert d["code"] == "it" and d["share"] < language.DOUBT and d["words"] == 260 and d["usual"] == language.DOUBT_USUAL
    assert language.doubt(text(BY_EAR), "xx") == {**d, "code": "", "share": language.doubt(text(BY_EAR), "")["share"]}   # no language stood out
    assert language.doubt(text(BY_EAR), "fr")["code"] == "fr"              # the language the user chose, measured the same way


def test_a_right_transcription_and_what_cannot_be_told_are_not():
    assert language.doubt(text(FRENCH), "fr") is None
    assert language.doubt(text(BY_EAR, 150), "it") is None                 # too short to tell
    assert language.doubt(text(BY_EAR), "ja") is None                      # no list for Japanese: nothing to compare with
    assert language.doubt(text(BY_EAR), "pl")["code"] == "pl"              # Polish has its list: judged
    assert language.doubt(text(FRENCH), "en")["code"] == "en"              # French taken for English: doubtful too


# --- the task of a video ---------------------------------------------------------------------------------------------

class Model:
    """A model that answers a code to the question of the language, and counts what it is asked."""

    def __init__(self, answer="ar"):
        self.answer, self.asked, self.prepared, self.finished = answer, [], 0, 0

    def prepare(self):
        self.prepared += 1

    def finish(self):
        self.finished += 1

    def complete(self, system, request, schema=None, effort=None):
        self.asked.append((system, request))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def a_video(tmp_path, said: str):
    """A video file with its transcription kept (no language given by the engine): `pipeline.prepare` takes it back."""
    video = tmp_path / "Episode 1.mp4"
    video.write_bytes(b"x")
    work = pipeline.work_dir(tmp_path, video)
    work.mkdir(parents=True)
    words, at = [], 0.0
    for sentence in said.replace(".", " .").split(" ."):
        for w in sentence.split():
            words.append({"word": w, "start": round(at, 2), "end": round(at + 0.4, 2)})
            at += 0.5
        at += 1.0
    (work / "transcription.json").write_text(json.dumps({"words": words, "segments": [], "text": said}), encoding="utf-8")
    return video


def a_queue(monkeypatch, tmp_path, translators, model, active="local"):
    cfg = {"output_dir": str(tmp_path), "transcriptions": [{"id": "t", "url": "http://127.0.0.1:8000"}], "active_transcription": "t",
           "translators": translators, "active_translator": active, "target_languages": ["fr"], "cleanup": {}}
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(translation, "create", lambda m, log=print: model)
    translated = []

    def translate(r, p, log, step, cancelled):
        translated.append(p.code)
        return pipeline.Result(p.video, p.source_srt, p.source_srt, p.code, len(p.cues), True)
    monkeypatch.setattr(pipeline, "translate_and_copy", translate)
    return TaskQueue(history=[]), translated


def a_task(q, tmp_path, video, **options):
    file = tmp_path / "dl-sub-dub.project.json"
    return q.add({"source": str(video), "project": "L", "project_file": str(file), "glossary": str(file.with_name("subtitles.glossary.json")),
                  "languages": ["fr"], "check_names": True, **options})


def test_the_video_of_a_project_waits_even_before_free_models(monkeypatch, tmp_path):
    video = a_video(tmp_path, text(BY_EAR))
    model = Model()
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], model)
    t = a_task(q, tmp_path, video, translator="local", spend_agreed=True)          # not a question of spending
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "language") and not translated and not model.asked
    assert any("langue douteuse" in line for line in t.log)
    e = q.estimate(t.id)
    assert e["waits"] == "language" and e["doubt"]["code"] == "it" and e["doubt"]["opinion"] is None
    assert e["translators"][0]["id"] == "local"                                    # what going on would take is shown with it
    q.resume(t.id)                                                                 # going on anyway: not asked twice
    q._run(t)
    assert t.state == TaskState.DONE and translated == ["it"]


def test_one_go_ahead_covers_the_doubt_and_the_costs(monkeypatch, tmp_path):
    video = a_video(tmp_path, text(BY_EAR))
    q, translated = a_queue(monkeypatch, tmp_path, [CLAUDE], Model(), active="claude")
    t = a_task(q, tmp_path, video, translator="claude")
    q._run(t)
    assert t.waits == "language" and q.estimate(t.id)["translators"][0]["cost_usd"] > 0     # its cost is shown with the warning
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and translated == ["it"]


def test_a_right_transcription_and_a_task_on_its_own_do_not_wait(monkeypatch, tmp_path):
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Model())
    t = a_task(q, tmp_path, a_video(tmp_path, text(FRENCH)), translator="local", languages=["en"])
    q._run(t)
    assert t.state == TaskState.DONE and translated == ["fr"] and not any("douteuse" in line for line in t.log)
    other = tmp_path / "alone"
    other.mkdir()
    t = q.add({"source": str(a_video(other, text(BY_EAR))), "translator": "local", "languages": ["fr"], "output_dir": str(other)})
    q._run(t)                                                                      # a script: warned in its log only
    assert t.state == TaskState.DONE and any("langue douteuse" in line for line in t.log)


def test_a_model_is_asked_which_language_is_spoken(monkeypatch, tmp_path):
    video = a_video(tmp_path, text(BY_EAR))
    model = Model("ar")
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE, NONE], model)
    t = a_task(q, tmp_path, video, translator="local")
    assert q.language_opinion(t.id, "claude") is None                              # it does not wait for that yet
    q._run(t)
    assert q.language_opinion(t.id, "claude") == {"code": "ar", "engine": "claude", "name": "Claude"}
    assert model.asked[0][0] == language.ASK_SPOKEN and (model.prepared, model.finished) == (1, 1)
    assert q.estimate(t.id)["doubt"]["opinion"]["code"] == "ar" and any("arabe" in line for line in t.log)
    assert t.state == TaskState.PAUSED and t.prepared.code == "it"                 # an opinion: the video keeps its language
    model.answer = "I cannot tell."
    assert q.language_opinion(t.id, "local")["code"] == ""
    try:
        q.language_opinion(t.id, "none")
    except translation.TranslationError:
        pass
    else:
        raise AssertionError("a choice that does not translate has no model to ask")


def test_the_estimate_of_a_project_names_its_doubtful_videos(monkeypatch, tmp_path):
    video = a_video(tmp_path, text(BY_EAR))
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], Model())
    g = Manager(q, start=False)
    run = Run(str(tmp_path), [{"path": str(video), "name": "Episode 1.mp4", "state": "todo"}], {"translator": "local"}, tmp_path / "project.json", "L")
    g.projects.all["L"] = run
    t = a_task(q, tmp_path, video, translator="local")
    q._run(t)
    update(run, 0, state="prepared", task=t.id)
    e = g.projects.estimate("L", ["fr"])
    assert e["waits"] == {"language": 1, "review": 0, "validation": 0, "translation": 0} and "doubt" not in e
    assert [(d["item"], d["title"], d["task"], d["code"]) for d in e["doubts"]] == [(run.items()[0].id, "Episode 1.mp4", t.id, "it")]
    assert [(m["id"], m["cost_usd"]) for m in e["opinion_models"]] == [("local", 0.0), ("claude", 0.001)]
    run.items()[0].selected = False
    e = g.projects.estimate("L", ["fr"])
    assert e["doubts"] == [] and "opinion_models" not in e
