"""The work of the service: the task queue and the orchestrator of the projects, put together, and what concerns both of them."""
from .. import i18n
from . import timing
from .orchestrator import Orchestrator
from .tasks import TaskQueue


class Manager:
    def __init__(self, queue: TaskQueue | None = None, start: bool = True):
        self.queue = queue or TaskQueue()
        self.projects = Orchestrator(self.queue)
        if start:
            self.queue.start()
            self.projects.start_feeding()

    def busy(self) -> bool:
        """A task runs or waits for its turn, or a project chains its videos."""
        return self.queue.current() is not None or self.projects.running() is not None

    def activity(self) -> str:
        """What is moving forward, in a few words ("local service" contract): "video 3/22" or the step of the task; empty if nothing."""
        run = self.projects.running()
        if run:
            return run.progress_text()
        t = self.queue.current()
        return (t.step or i18n.t("activity.task")) if t else ""

    def remaining_s(self) -> int | None:
        """Seconds before the end of the running work (project, otherwise single task), if it can be estimated."""
        run = self.projects.running()
        if run:
            return run.remaining_s(timing.known_rate(run.phase, run.options))
        t = self.queue.current()
        return t.remaining_s() if t else None

    def close(self):
        self.queue.close()
