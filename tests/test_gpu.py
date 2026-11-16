"""The graphics card shared by a local translation model and the dubbing: the setting, freeing the card before the
dubbing (addresses to call, Ollama, or the user), the check of the card, the message when its memory runs out."""
import pytest

from helpers import at
from subtitles.web import background
from subtitles.web import observed
from subtitles import config, dubbing, gpu, translation
from subtitles.project import Stage, StageState
from subtitles.states import RunState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

LOCAL = {"id": "local", "type": "openai", "name": "Qwen", "url": "http://127.0.0.1:8080", "model": "qwen"}
OLLAMA = {"id": "ollama", "type": "openai", "name": "Ollama", "url": "http://localhost:11434/v1", "model": "qwen3"}
REMOTE = {"id": "home", "type": "openai", "name": "Another machine", "url": "http://192.0.2.20:8080", "model": "qwen"}


def cfg_with(**dub):
    return {"dubbing": {"enabled": True, "command": ["python"], "model": "m", **dub}}


def test_the_setting_and_what_frees_the_card():
    assert gpu.sharing(cfg_with()) == "one_at_a_time"                                    # auto, no check yet: cautious
    assert gpu.sharing(cfg_with(gpu_test={"fits": True})) == "together"
    assert gpu.sharing(cfg_with(gpu_test={"fits": False})) == "one_at_a_time"
    assert gpu.sharing(cfg_with(gpu_sharing="together", gpu_test={"fits": False})) == "together"
    assert gpu.is_local(LOCAL) and gpu.is_local(OLLAMA) and not gpu.is_local(REMOTE)
    assert not gpu.is_local({"id": "claude", "type": "claude"}) and not gpu.is_local(None)
    assert gpu.how_to_free(cfg_with(), LOCAL) == "ask"
    assert gpu.how_to_free(cfg_with(), OLLAMA) == "ollama"
    assert gpu.how_to_free(cfg_with(free_gpu={"stop": {"url": "http://127.0.0.1:8300/api/ai/stop"}}), LOCAL) == "hooks"
    assert gpu.how_to_free(cfg_with(), REMOTE) == ""                                     # not on this card
    assert gpu.how_to_free(cfg_with(), {**LOCAL, "supervision": "http://127.0.0.1:8300"}) == "supervision"
    assert gpu.how_to_free(cfg_with(gpu_sharing="together"), LOCAL) == ""


def test_freeing_calls_the_addresses_or_unloads_ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_post", lambda call, timeout=30: calls.append(call))
    monkeypatch.setattr(gpu.time, "sleep", lambda s: None)
    hooks = {"stop": {"url": "http://127.0.0.1:8300/api/ai/stop"}, "start": {"url": "http://127.0.0.1:8300/api/ai/start", "body": {"engine": "llamacpp"}}}
    assert gpu.free(cfg_with(free_gpu=hooks), LOCAL, log=lambda m: None)
    gpu.restore(cfg_with(free_gpu=hooks), log=lambda m: None)
    assert gpu.free(cfg_with(), OLLAMA, log=lambda m: None)
    assert calls == [hooks["stop"], hooks["start"],
                     {"url": "http://localhost:11434/api/generate", "body": {"model": "qwen3", "keep_alive": 0}}]
    assert not gpu.free(cfg_with(), LOCAL, log=lambda m: None)                           # the user has to do it
    calls.clear()
    assert gpu.free(cfg_with(), {**LOCAL, "supervision": "http://127.0.0.1:8300/"}, log=lambda m: None)
    assert calls == [{"url": "http://127.0.0.1:8300/api/ai/stop"}]                     # started again by the next translation


def test_a_full_card_is_said_clearly():
    assert dubbing.is_out_of_memory("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB")
    assert not dubbing.is_out_of_memory("empty subtitles")


def test_the_check_measures_the_card(monkeypatch, tmp_path):
    memory = iter([(500, 8151), (4800, 8151)])                  # before, then the model loaded
    monkeypatch.setattr(gpu, "read_memory", lambda: next(memory, (7900, 8151)))
    monkeypatch.setattr(translation, "test", lambda m: "prêt")
    monkeypatch.setattr(gpu, "make_sample", lambda folder, video, language: (folder / "a.m4a", folder / "a.srt", folder / "b.srt"))
    monkeypatch.setattr(dubbing, "dub", lambda *a, **k: tmp_path / "d.m4a")
    r = gpu.run_test(cfg_with(), LOCAL)
    assert r["local"] and r["model_mib"] == 4800 and r["peak_mib"] == 7900 and r["total_mib"] == 8151 and r["fits"] is False
    memory = iter([(500, 8151), (3000, 8151)])
    monkeypatch.setattr(gpu, "read_memory", lambda: next(memory, (6000, 8151)))
    assert gpu.run_test(cfg_with(), LOCAL)["fits"] is True                               # 2151 MiB left: fits

    def full(*a, **k):
        raise dubbing.DubbingError("CUDA out of memory")
    monkeypatch.setattr(dubbing, "dub", full)
    assert gpu.run_test(cfg_with(), LOCAL)["fits"] is False


@pytest.fixture
def g(tmp_path, monkeypatch):
    state = {"cfg": {"output_dir": str(tmp_path / "out"), "target_languages": ["fr"], "translators": [LOCAL], "active_translator": "local",
                     "dubbing": {"enabled": True, "command": ["python"], "model": "m"}}}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: state["cfg"])
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    m = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(background, "spawn", lambda name, target, *args: target(*args))
    m.state = state
    return m


def subtitled(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    item = run.project.items[0]
    at(item, Stage.SUBTITLES, StageState.DONE)
    item.facts["translated"] = True
    return run, item


def test_the_project_waits_for_the_card_then_dubs(g, tmp_path):
    run, item = subtitled(g, tmp_path)
    run.phase = 3
    assert g.projects.start(run.id, [Stage.DUBBING], only=True)
    run.phase = 3
    g.projects.feed()
    assert run.state == RunState.SUSPENDED and run.card.wait and item.state(Stage.DUBBING) == StageState.TODO
    assert run.summary()["dubbing"]["gpu"]["wait"] and run.summary()["dubbing"]["gpu"]["free"] == "ask"
    assert g.projects.gpu_freed(run.id)
    assert run.state == RunState.RUNNING and not run.card.wait and "gpu_wait" not in run.options
    g.projects.feed()
    assert item.state(Stage.DUBBING) == StageState.RUNNING


def test_one_line_waits_for_the_card(g, tmp_path):
    run, item = subtitled(g, tmp_path)
    assert g.projects.run_item(run.id, item.id, Stage.DUBBING)                 # accepted, waiting for the card
    assert item.state(Stage.DUBBING) == StageState.TODO and run.card.waiting == [item.id] and "gpu_wait_items" not in run.options
    assert g.projects.gpu_freed(run.id) and item.state(Stage.DUBBING) == StageState.RUNNING


def test_the_card_freed_and_given_back_by_the_addresses(g, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_post", lambda call, timeout=30: calls.append(call["url"]))
    monkeypatch.setattr(gpu.time, "sleep", lambda s: None)
    g.state["cfg"]["dubbing"]["free_gpu"] = {"stop": {"url": "http://x/stop"}, "start": {"url": "http://x/start"}}
    run, item = subtitled(g, tmp_path)
    assert g.projects.start(run.id, [Stage.DUBBING], only=True)
    run.phase = 3
    g.projects.feed()
    assert calls == ["http://x/stop"] and item.state(Stage.DUBBING) == StageState.RUNNING and run.card.restore
    task = g.queue.get(item.stages[Stage.DUBBING].task)
    task.state = "done"
    g.projects.task_changed(task)
    g.projects.feed()
    assert run.state == RunState.DONE and calls == ["http://x/stop", "http://x/start"] and not run.card.restore


def test_shared_card_nothing_to_free(g, tmp_path):
    g.state["cfg"]["dubbing"]["gpu_sharing"] = "together"
    run, item = subtitled(g, tmp_path)
    assert g.projects.run_item(run.id, item.id, Stage.DUBBING) and item.state(Stage.DUBBING) == StageState.RUNNING
