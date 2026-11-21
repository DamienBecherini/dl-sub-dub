"""The sure fixes of the AI review applied without a question (`review_auto`, for who cannot judge them: a video in a
language one does not read): only what the model says sure, never a removed line, the rest left to decide without
stopping the video; a paid review still waits for its cost; what was applied is listed and taken back."""
import dataclasses
import json

from subtitles import corrections, glossary, pipeline, review
from subtitles.states import TaskState
from subtitles.web.manager import Manager
from helpers import Run
from test_full_review import CLAUDE, LOCAL, Reviewer, a_queue, a_task, fix, project_folder

FIXES = [fix(1, "We met Peter in the laboratory."), fix(2, "He works on the genomes.", "medium"), fix(3, "")]     # sure, less sure, a line removed


def test_the_sure_fixes_are_applied_and_the_video_does_not_wait(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([FIXES])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], model)
    t = a_task(q, tmp_path, video, review="full", translator="local", review_auto=True)
    q._run(t)
    assert t.state == TaskState.DONE and len(model.requests) == 1                      # reviewed once, no pause for its proposals
    assert translated == [["We met Peter in the laboratory.", "He works on the genome.", "Thank you for watching!", "The cells emit light."]]
    (applied,) = corrections.applied_alone(work)
    assert (applied["line"], applied["auto"], applied["status"]) == (1, True, "accepted")
    assert sorted(c["line"] for c in corrections.pending(work)) == [2, 3]              # the less sure one, and the line removed: left to decide
    said = "\n".join(t.log)
    assert "1 correction sûre appliquée sans vous demander" in said and "2 propositions moins sûres ne sont pas appliquées" in said
    assert t.fixed_alone                                                               # told to the project, which tells the page


def test_without_the_setting_every_fix_waits_for_a_decision(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Reviewer([FIXES]))
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "validation") and not corrections.applied_alone(work) and not translated


def test_a_paid_review_still_waits_for_its_cost(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([FIXES])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    t = a_task(q, tmp_path, video, review="full", translator="local", review_auto=True)       # Claude reviews: paid
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "review") and not model.requests           # nothing spent without a go-ahead
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and translated[0][0] == "We met Peter in the laboratory."


def test_the_fixes_applied_before_a_paid_translation_are_in_what_is_translated(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], Reviewer([FIXES]), active="claude")
    t = a_task(q, tmp_path, video, review="full", review_engine="local", translator="claude", review_auto=True)
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "translation") and len(corrections.applied_alone(work)) == 1
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and translated[0][0] == "We met Peter in the laboratory."
    assert sum("correction sûre appliquée" in line for line in t.log) == 1                    # said once, not at each pass


def prepares(monkeypatch) -> list[bool]:
    """Whether each preparation of a video is asked to transcribe again (`redo`)."""
    asked, real = [], pipeline.prepare

    def prepare(r, *a, **k):
        asked.append(r.redo)
        return real(dataclasses.replace(r, redo=False), *a, **k)               # the test has no transcription server: its transcription is kept
    monkeypatch.setattr(pipeline, "prepare", prepare)
    return asked


def test_a_video_asked_again_is_not_transcribed_twice_for_its_fixes(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Reviewer([FIXES]))
    asked = prepares(monkeypatch)
    t = a_task(q, tmp_path, video, review="full", translator="local", review_auto=True, redo=True)
    q._run(t)
    assert t.state == TaskState.DONE and asked == [True, False]                # cut again with the fixes, from the transcription just made


def test_nor_for_the_fixes_decided_at_its_pause(monkeypatch, tmp_path):
    """Before, the second preparation transcribed again, and the fixes just accepted went with the old transcription."""
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Reviewer([FIXES]))
    asked = prepares(monkeypatch)
    t = a_task(q, tmp_path, video, review="full", translator="local", redo=True)
    q._run(t)
    assert t.waits == "validation"
    corrections.decide(work, [{"id": corrections.pending(work)[0]["id"], "accept": True}])
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and asked == [True, False] and translated[0][0] == "We met Peter in the laboratory."


def test_a_fix_applied_is_taken_back(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Reviewer([FIXES]))
    q._run(a_task(q, tmp_path, video, review="full", translator="local", review_auto=True))
    (applied,) = corrections.applied_alone(work)
    before = corrections.fingerprint(work)
    assert corrections.take_back(work, [applied["id"], "c-unknown"]) == 1
    assert not corrections.applied_alone(work) and not corrections.accepted(work) and corrections.fingerprint(work) != before
    assert corrections.take_back(work, [applied["id"]]) == 0                                   # once
    waiting = corrections.pending(work)[0]
    corrections.decide(work, [{"id": waiting["id"], "accept": True}])
    assert corrections.take_back(work, [waiting["id"]]) == 0 and len(corrections.accepted(work)) == 1     # only what was applied alone


def proposals(tmp_path, entries):
    file = tmp_path / "dl-sub-dub.project.json"
    review.path_for(file).write_text(json.dumps({"examined": [e["transcribed"] for e in entries], "proposals": entries}), encoding="utf-8")
    return file


def name(transcribed, spelling, status="to_fix", confidence="high", french=""):
    return {"transcribed": transcribed, "status": status, "spelling": spelling, "french": french, "confidence": confidence,
            "reason": "son proche", "occurrences": 2, "examples": []}


def test_only_the_names_recognised_and_sure_are_accepted(tmp_path):
    file = proposals(tmp_path, [name("Gary Iev", "Gariaev"), name("Plato", "Plato", "french_form", french="Platon"),
                                name("Pjotr", "Piotr", confidence="medium"), name("Zorglub", "", "uncertain"),
                                name("Elsewhere", "Somewhere")])
    assert review.accept_sure(file, ["Gary Iev", "Plato", "Pjotr", "Zorglub"]) == ["Gary Iev", "Plato"]      # not the name of another video
    kept = {e["transcribed"]: e for e in glossary.load(glossary.path_for(file))}
    assert set(kept) == {"Gary Iev", "Plato"} and all(e["auto"] for e in kept.values()) and kept["Plato"]["french"] == "Platon"
    assert review.pending_names(file) == {"Pjotr", "Zorglub", "Elsewhere"}                     # left to decide
    assert [e["transcribed"] for e in review.applied_alone(file)] == ["Gary Iev", "Plato"]
    assert review.take_back(file, ["Gary Iev", "Nobody"]) == 1
    assert [e["transcribed"] for e in glossary.load(glossary.path_for(file))] == ["Plato"]
    assert "Gary Iev" not in review.pending_names(file)                                        # taken back: not asked again


def test_a_name_validated_by_the_user_is_not_taken_back_as_an_automatic_one(tmp_path):
    file = proposals(tmp_path, [name("Gary Iev", "Gariaev")])
    review.validate(file, [{"transcribed": "Gary Iev", "spelling": "Gariaev", "french": "", "accept": True}])
    assert not review.applied_alone(file) and review.take_back(file, ["Gary Iev"]) == 0
    assert len(glossary.load(glossary.path_for(file))) == 1


def test_a_project_goes_to_its_end_and_lists_what_was_applied(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], Reviewer([FIXES]))
    g = Manager(q, start=False)
    run = Run(str(tmp_path), [{"path": video.as_posix(), "state": "todo"}],
              {"translator": "local", "languages": ["fr"], "review": "full", "review_auto": True, "output_dir": str(tmp_path)},
              tmp_path / "dl-sub-dub.project.json", "L")
    g.projects.all["L"] = run
    g.projects.feed()
    for t in list(q.tasks.values()):
        q._run(t)
    g.projects.feed()
    assert run.state == "done" and run.review_stamp == 1 and run.subtitle_settings()["review_auto"] is True
    found = g.projects.proposals("L")
    (mine,) = found["applied"]["corrections"]
    assert [c["line"] for c in mine["corrections"]] == [1] and found["applied"]["names"] == []
    assert [c["line"] for c in found["corrections"][0]["corrections"]] == [2, 3]               # left to decide, shown as before
    undo = {"lines": [{"item": mine["item"], "id": mine["corrections"][0]["id"]}, {"item": "nobody", "id": "c1"}], "names": ["Nobody"]}
    assert g.projects.validate_names("L", [], [], undo)["taken_back"] == 1
    assert g.projects.proposals("L")["applied"] == {"names": [], "corrections": []} and run.review_stamp == 2
    assert g.projects.validate_names("L", [], [], "not a dict")["taken_back"] == 0
