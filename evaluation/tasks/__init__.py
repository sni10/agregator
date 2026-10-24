from .card_check import CardCheckTask
from .classify import ClassifyTask
from .extract import ExtractTask
from .same_event import SameEventTask
from .verify import VerifyTask
from .write import WriteTask

TASKS = {task.name: task for task in (ClassifyTask, SameEventTask, ExtractTask, VerifyTask, WriteTask, CardCheckTask)}
